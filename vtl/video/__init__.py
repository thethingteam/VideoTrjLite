"""Video backends: PyAV first, OpenCV as a fallback."""
from __future__ import annotations

import os

from .base import VideoSource

VIDEO_EXTENSIONS = (
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".ts", ".mpg", ".mpeg",
    ".wmv", ".webm", ".flv", ".3gp", ".mxf", ".insv", ".lrv", ".dv", ".vob", ".ogv",
)


def open_video(path: str) -> VideoSource:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Video file not found: {path}")
    errors = []
    try:
        from .pyav_source import PyAVSource

        return PyAVSource(path)
    except Exception as exc:  # noqa: BLE001 - try the fallback
        errors.append(f"PyAV: {exc}")
    try:
        from .opencv_source import OpenCVSource

        return OpenCVSource(path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"OpenCV: {exc}")
    raise ValueError("Could not open video. " + " | ".join(errors))


__all__ = ["open_video", "VideoSource", "VIDEO_EXTENSIONS"]
