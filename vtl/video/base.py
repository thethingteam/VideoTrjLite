from __future__ import annotations

from typing import Iterator, Protocol

import numpy as np

from ..models import VideoInfo


class VideoSource(Protocol):
    info: VideoInfo

    def frame_at(self, pts: float) -> tuple[float, np.ndarray]:
        """Accurately seek; return (actual_pts, RGB uint8 image, rotation applied)."""
        ...

    def iter_raw(self, start: float, end: float) -> Iterator[tuple[float, object]]:
        """Sequentially decode frames around [start, end].

        Yields (pts, raw_frame); call ``convert(raw_frame)`` only for frames
        that are actually exported, so unused frames skip RGB conversion.
        """
        ...

    def convert(self, raw: object) -> np.ndarray:
        """raw frame -> RGB uint8 image with rotation applied."""
        ...

    def close(self) -> None: ...


def apply_rotation(img: np.ndarray, rotation_cw: int) -> np.ndarray:
    r = rotation_cw % 360
    if r == 0:
        return img
    if r == 90:
        return np.ascontiguousarray(np.rot90(img, k=-1))
    if r == 180:
        return np.ascontiguousarray(img[::-1, ::-1])
    if r == 270:
        return np.ascontiguousarray(np.rot90(img, k=1))
    return img


def snap_rotation(deg: float) -> int:
    """Round arbitrary degrees to the nearest multiple of 90 in [0, 360)."""
    return int(round(deg / 90.0)) * 90 % 360
