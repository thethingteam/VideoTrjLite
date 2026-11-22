"""Export pipeline: schedule -> sequential decode -> images + CSV + summary."""
from __future__ import annotations

import csv
import json
import os
import threading
from collections import Counter, deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Optional

from PIL import Image

from . import __version__
from .align import alignment_to_dict
from .geotag import build_exif
from .models import (
    DUPLICATE_FRAME,
    FRAME_UNAVAILABLE,
    OK,
    Alignment,
    ExportConfig,
    ExportResult,
    SampleSlot,
    Track,
)
from .sampler import build_schedule, exportable
from .timefmt import filename_stamp, fmt_iso_utc, fmt_local, format_tz, relative_stamp
from .track.interp import TrackInterpolator
from .video.base import VideoSource

ProgressFn = Callable[[int, int, str], None]

FRAME_COLUMNS = [
    "image_file", "video_pts", "utc_time", "local_time", "latitude", "longitude", "altitude",
    "speed_mps", "course_deg", "fix", "hdop", "ref_time_pre", "ref_time_next",
]
SKIP_COLUMNS = ["slot", "video_pts", "utc_time", "local_time", "reason", "detail"]


def make_job_dir(output_root: str) -> str:
    os.makedirs(output_root, exist_ok=True)
    base = os.path.join(output_root, "vtl_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    path, n = base, 2
    while os.path.exists(path):
        path = f"{base}_{n}"
        n += 1
    os.makedirs(os.path.join(path, "images"))
    return path


def _fmt(v: Optional[float], nd: int) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def _image_name(utc: Optional[float], pts: float, cfg: ExportConfig, video_start: float, ext: str) -> str:
    if utc is not None and not cfg.relative_time:
        stem = filename_stamp(utc, cfg.tz_offset_min)
    else:
        stem = relative_stamp(pts - video_start, cfg.relative_date or "00000000")
    return f"{stem}.{ext}"


def run_export(
    source: VideoSource,
    track: Optional[Track],
    alignment: Optional[Alignment],
    cfg: ExportConfig,
    output_root: str,
    progress: Optional[ProgressFn] = None,
    cancel: Optional[threading.Event] = None,
    extra_summary: Optional[dict] = None,
) -> ExportResult:
    info = source.info
    interp = TrackInterpolator(track, cfg.max_gap_s) if track is not None else None
    if alignment is None:
        cfg.relative_time = True
    slots = build_schedule(info, alignment, interp, cfg)
    if not slots:
        raise ValueError("Nothing to export in the selected range")
    todo = [s for s in slots if exportable(s)]

    job = make_job_dir(output_root)
    img_dir = os.path.join(job, "images")
    ext = "png" if cfg.image_format.lower() == "png" else "jpg"
    names: dict[int, str] = {}  # slot index -> image file, filled as frames are written
    used: set[str] = set()

    def name_for(slot: SampleSlot, pts: float) -> str:
        base = _image_name(slot.utc, pts, cfg, info.start_pts, ext)
        name, k = base, 2
        while name in used:
            stem, e = os.path.splitext(base)
            name = f"{stem}_{k}{e}"
            k += 1
        used.add(name)
        names[slot.index] = name
        return name

    report = progress or (lambda done, total, msg: None)
    total = len(todo)
    report(0, total, f"Exporting {total} images")

    pool = ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4) // 2)))
    pending: deque[Future] = deque()
    written = 0
    cancelled = False

    def save(img_arr, path: str, slot: SampleSlot) -> None:
        im = Image.fromarray(img_arr)
        kwargs: dict = {}
        if cfg.write_exif:
            kwargs["exif"] = build_exif(slot.fix, slot.utc, cfg.tz_offset_min)
        if ext == "jpg":
            im.save(path, "JPEG", quality=cfg.jpeg_quality, subsampling=0, **kwargs)
        else:
            im.save(path, "PNG", **kwargs)

    def drain(limit: int) -> None:
        nonlocal written
        while len(pending) > limit:
            pending.popleft().result()
            written += 1
            if written % 5 == 0 or written == total:
                report(written, total, f"{written}/{total} images")

    # Nearest decoded frame is used; only reject when clearly no frame exists
    # (truncated files, large VFR holes).
    tol = max(info.frame_duration * 2.0, 0.05)
    i = 0
    prev: Optional[tuple[float, object]] = None
    last_pts: Optional[float] = None  # PTS of the last frame written
    # In distance mode the candidate grid is not the real frame grid (VFR),
    # so two kept slots can resolve to one frame; write it only once.
    no_dupes = cfg.distance_m > 0

    def emit(slot: SampleSlot, cand: Optional[tuple[float, object]]) -> None:
        nonlocal last_pts
        if cand is None or abs(cand[0] - slot.pts) > tol:
            slot.status = FRAME_UNAVAILABLE
            slot.reason = "no decoded frame near this time"
            return
        if no_dupes and last_pts is not None and cand[0] == last_pts:
            slot.status = DUPLICATE_FRAME
            slot.reason = f"same frame as the previous image ({cand[0]:.4f} s)"
            return
        last_pts = cand[0]
        slot.pts_actual = cand[0]  # type: ignore[attr-defined]
        # Tag the image with the time and position of the frame actually
        # written, not of the scheduled instant (they differ by up to ``tol``).
        if alignment is not None:
            utc = alignment.to_utc(cand[0])
            if interp is None:
                slot.utc = utc
            else:
                status, fix = interp.locate(utc)
                if status == OK and fix is not None:  # else keep the slot's own time + fix
                    slot.utc, slot.fix = utc, fix
        arr = source.convert(cand[1])
        pending.append(pool.submit(save, arr, os.path.join(img_dir, name_for(slot, cand[0])), slot))
        drain(16)

    try:
        if todo:
            for fp, raw in source.iter_raw(todo[0].pts, todo[-1].pts):
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                while i < len(todo) and todo[i].pts <= fp:
                    s = todo[i]
                    best = (fp, raw)
                    if prev is not None and abs(prev[0] - s.pts) <= abs(fp - s.pts):
                        best = prev
                    emit(s, best)
                    i += 1
                prev = (fp, raw)
                if i >= len(todo):
                    break
            if not cancelled:
                while i < len(todo):
                    emit(todo[i], prev)
                    i += 1
        drain(0)
    finally:
        pool.shutdown(wait=True)

    # ------------------------------------------------------------- outputs
    tz = cfg.tz_offset_min
    exported_slots = [s for s in todo if exportable(s) and s.index in names]
    if cancelled:
        exported_slots = [
            s for s in exported_slots
            if os.path.exists(os.path.join(img_dir, names[s.index]))
        ]
    with open(os.path.join(job, "frames.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(FRAME_COLUMNS)
        for s in exported_slots:
            f = s.fix
            pts = getattr(s, "pts_actual", s.pts)
            w.writerow([
                names[s.index],
                f"{pts:.4f}",
                fmt_iso_utc(s.utc) if s.utc is not None else "",
                fmt_local(s.utc, tz) if s.utc is not None else "",
                _fmt(f.lat if f else None, 8),
                _fmt(f.lon if f else None, 8),
                _fmt(f.alt if f else None, 3),
                _fmt(f.speed if f else None, 3),
                _fmt(f.course if f else None, 1),
                (f.fix or "") if f else "",
                _fmt(f.hdop if f else None, 2),
                fmt_iso_utc(f.ref_pre) if f else "",
                fmt_iso_utc(f.ref_next) if f else "",
            ])

    skipped = [s for s in slots if not exportable(s)]
    with open(os.path.join(job, "skipped.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(SKIP_COLUMNS)
        for s in skipped:
            w.writerow([
                s.index,
                f"{s.pts:.4f}",
                fmt_iso_utc(s.utc) if s.utc is not None else "",
                fmt_local(s.utc, tz) if s.utc is not None else "",
                s.status,
                s.reason,
            ])

    counts = Counter(s.status for s in skipped)
    summary = {
        "tool": "video_trj_integration_lite",
        "version": __version__,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cancelled": cancelled,
        "video": {
            "path": info.path,
            "backend": info.backend,
            "codec": info.codec,
            "width": info.width,
            "height": info.height,
            "avg_fps": info.avg_fps,
            "is_vfr": info.is_vfr,
            "rotation": info.rotation,
            "start_pts": info.start_pts,
            "duration": info.duration,
        },
        "track": None if track is None else {
            "path": track.path,
            "format": track.source,
            "points": len(track),
            "start_utc": fmt_iso_utc(track.start),
            "end_utc": fmt_iso_utc(track.end),
            "warnings": track.warnings,
        },
        "alignment": None if alignment is None else alignment_to_dict(alignment),
        "time_mode": "relative" if cfg.relative_time else "clock",
        "timezone": format_tz(tz),
        "config": {
            "rate": None if cfg.distance_m > 0 else cfg.rate,
            "distance_m": cfg.distance_m or None,
            "start_offset": cfg.start_offset,
            "in_pts": cfg.in_pts,
            "out_pts": cfg.out_pts,
            "max_gap_s": cfg.max_gap_s,
            "min_move_m": cfg.min_move_m,
            "image_format": ext,
            "jpeg_quality": cfg.jpeg_quality,
            "write_exif": cfg.write_exif,
        },
        "counts": {
            "slots": len(slots),
            "exported": len(exported_slots),
            "skipped": dict(counts),
        },
    }
    if extra_summary:
        summary.update(extra_summary)
    with open(os.path.join(job, "run_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    if alignment is not None:
        with open(os.path.join(job, "alignment.json"), "w", encoding="utf-8") as fh:
            json.dump({**alignment_to_dict(alignment), "timezone": format_tz(tz),
                       "anchor_utc_iso": fmt_iso_utc(alignment.anchor_utc)}, fh, indent=2)

    report(len(exported_slots), total, "Cancelled" if cancelled else "Done")
    return ExportResult(job, len(exported_slots), dict(counts), len(slots), cancelled)
