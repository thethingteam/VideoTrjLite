"""PyAV (bundled FFmpeg) video backend - the primary backend."""
from __future__ import annotations

import statistics
from typing import Iterator, Optional

import av
import numpy as np

from ..models import VideoInfo
from .base import apply_rotation, snap_rotation

_PROBE_PACKETS = 400


def _parse_creation_time(meta: dict) -> Optional[float]:
    from ..track.gpx import parse_iso8601

    for key in ("creation_time", "com.apple.quicktime.creationdate", "date"):
        v = meta.get(key)
        if v:
            try:
                return parse_iso8601(v)
            except ValueError:
                continue
    return None


class PyAVSource:
    def __init__(self, path: str):
        self.path = path
        self._c = av.open(path)
        if not self._c.streams.video:
            self._c.close()
            raise ValueError("File has no video stream")
        self._s = self._c.streams.video[0]
        self._s.thread_type = "AUTO"
        self._tb = float(self._s.time_base)
        self._cache: Optional[tuple[float, np.ndarray]] = None
        self.info = self._probe()

    # ------------------------------------------------------------------ probe
    def _probe(self) -> VideoInfo:
        s, c = self._s, self._c
        # Packet timestamps (no decoding) tell us start, frame spacing and VFR.
        pts_list: list[float] = []
        try:
            for pkt in c.demux(s):
                if pkt.pts is not None:
                    pts_list.append(pkt.pts * self._tb)
                if len(pts_list) >= _PROBE_PACKETS:
                    break
        except av.FFmpegError:
            pass
        pts_list.sort()
        self._rewind()

        start = float(s.start_time * self._tb) if s.start_time is not None else (
            pts_list[0] if pts_list else 0.0
        )
        if s.duration is not None:
            duration = float(s.duration * self._tb)
        elif c.duration is not None:
            duration = c.duration / 1_000_000.0
        else:
            duration = 0.0

        avg = float(s.average_rate) if s.average_rate else 0.0
        if avg <= 0 and s.guessed_rate:
            avg = float(s.guessed_rate)
        is_vfr: Optional[bool] = None
        if len(pts_list) > 10:
            diffs = [b - a for a, b in zip(pts_list, pts_list[1:]) if b > a]
            if diffs:
                med = statistics.median(diffs)
                if avg <= 0 and med > 0:
                    avg = 1.0 / med
                spread = (max(diffs) - min(diffs)) / med if med > 0 else 0
                is_vfr = spread > 0.25
        if avg <= 0:
            avg = 30.0

        rotation = 0
        first = None
        try:
            for frame in c.decode(s):
                first = frame
                break
        except av.FFmpegError:
            pass
        if first is not None:
            r = getattr(first, "rotation", 0) or 0
            # FFmpeg display matrix angle is counter-clockwise; we store CW.
            rotation = snap_rotation(-float(r))
            if not pts_list and first.pts is not None:
                start = first.pts * self._tb
        if rotation == 0:
            tag = s.metadata.get("rotate")
            if tag:
                try:
                    rotation = snap_rotation(float(tag))
                except ValueError:
                    pass
        self._rewind()

        w, h = s.codec_context.width, s.codec_context.height
        if rotation in (90, 270):
            w, h = h, w
        creation = _parse_creation_time(dict(c.metadata)) or _parse_creation_time(dict(s.metadata))
        codec = s.codec_context.name or "?"
        if duration <= 0 and pts_list:
            duration = pts_list[-1] - start + 1.0 / avg
        return VideoInfo(
            path=self.path,
            duration=duration,
            start_pts=start,
            end_pts=start + duration,
            avg_fps=avg,
            is_vfr=is_vfr,
            width=w,
            height=h,
            rotation=rotation,
            codec=codec,
            creation_time=creation,
            backend="pyav",
        )

    def _rewind(self) -> None:
        try:
            self._c.seek(0, backward=True, any_frame=False)
        except av.FFmpegError:
            # Some streams can't seek to 0; reopen.
            self._c.close()
            self._c = av.open(self.path)
            self._s = self._c.streams.video[0]
            self._s.thread_type = "AUTO"

    def _to_rgb(self, frame: "av.VideoFrame") -> np.ndarray:
        return apply_rotation(frame.to_ndarray(format="rgb24"), self.info.rotation)

    def _frame_pts(self, frame) -> Optional[float]:
        if frame.pts is not None:
            return frame.pts * self._tb
        t = frame.time
        return float(t) if t is not None else None

    # ---------------------------------------------------------------- access
    def _decode_from(self, target: float) -> Iterator[tuple[float, object]]:
        """Seek so that decoding starts at or before ``target`` and yield (pts, frame).

        Some containers (notably MPEG-TS) land on a keyframe *after* the
        requested time; in that case back off further and retry.
        """
        start = self.info.start_pts
        back = 0.0
        eps = 0.25 / self.info.avg_fps
        while True:
            seek_t = max(start, target - back)
            self._c.seek(int(seek_t / self._tb), stream=self._s, backward=True, any_frame=False)
            gen = self._c.decode(self._s)
            first = None
            for frame in gen:
                fp = self._frame_pts(frame)
                if fp is not None:
                    first = (fp, frame)
                    break
            if first is None:
                return
            if first[0] <= target + eps or seek_t <= start or back > 60:
                yield first
                for frame in gen:
                    fp = self._frame_pts(frame)
                    if fp is not None:
                        yield fp, frame
                return
            back = 1.0 if back == 0 else back * 2

    def frame_at(self, pts: float) -> tuple[float, np.ndarray]:
        info = self.info
        pts = min(max(pts, info.start_pts), max(info.start_pts, info.end_pts - 1e-3))
        if self._cache and abs(self._cache[0] - pts) < 1e-6:
            return self._cache
        half = 0.5 / info.avg_fps
        best: Optional[tuple[float, object]] = None
        for fp, frame in self._decode_from(pts):
            if best is None or abs(fp - pts) < abs(best[0] - pts):
                best = (fp, frame)
            if fp >= pts + half:
                break
        if best is None:
            raise ValueError(f"No frame could be decoded near {pts:.3f}s")
        result = (best[0], self._to_rgb(best[1]))  # type: ignore[arg-type]
        self._cache = result
        return result

    def iter_raw(self, start: float, end: float) -> Iterator[tuple[float, object]]:
        margin = 2.0 / self.info.avg_fps
        self._cache = None
        for fp, frame in self._decode_from(max(self.info.start_pts, start - margin)):
            if fp < start - margin:
                continue
            if fp > end + margin:
                break
            yield fp, frame

    def convert(self, raw: object) -> np.ndarray:
        return self._to_rgb(raw)  # type: ignore[arg-type]

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass
