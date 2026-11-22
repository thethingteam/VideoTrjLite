"""OpenCV fallback backend (used only when PyAV cannot open a file)."""
from __future__ import annotations

from typing import Iterator, Optional

import numpy as np

from ..models import VideoInfo


class OpenCVSource:
    def __init__(self, path: str):
        import cv2

        self._cv2 = cv2
        self.path = path
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise ValueError("OpenCV could not open the video")
        fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = n / fps if fps > 0 else 0.0
        self._cache: Optional[tuple[float, np.ndarray]] = None
        self.info = VideoInfo(
            path=path,
            duration=duration,
            start_pts=0.0,
            end_pts=duration,
            avg_fps=fps,
            is_vfr=None,
            width=w,
            height=h,
            rotation=0,  # OpenCV applies orientation itself when supported
            codec="?",
            creation_time=None,
            backend="opencv",
        )

    def _rgb(self, bgr: np.ndarray) -> np.ndarray:
        return self._cv2.cvtColor(bgr, self._cv2.COLOR_BGR2RGB)

    def frame_at(self, pts: float) -> tuple[float, np.ndarray]:
        cv2 = self._cv2
        idx = max(0, int(round(pts * self.info.avg_fps)))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self._cap.read()
        if not ok:
            raise ValueError(f"No frame could be decoded near {pts:.3f}s")
        return idx / self.info.avg_fps, self._rgb(frame)

    def iter_raw(self, start: float, end: float) -> Iterator[tuple[float, object]]:
        cv2 = self._cv2
        fps = self.info.avg_fps
        idx = max(0, int(start * fps) - 2)
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        while True:
            ok, frame = self._cap.read()
            if not ok:
                break
            pts = idx / fps
            idx += 1
            if pts > end + 2 / fps:
                break
            yield pts, frame

    def convert(self, raw: object) -> np.ndarray:
        return self._rgb(raw)  # type: ignore[arg-type]

    def close(self) -> None:
        self._cap.release()
