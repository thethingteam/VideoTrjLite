"""Track loading with format dispatch.

Readers are registered in ``READERS``; adding a format means adding a module
and one entry here.
"""
from __future__ import annotations

import os
from datetime import date
from typing import Callable, Optional

from ..models import Track
from .gpx import parse_gpx
from .nmea import parse_nmea

READERS: dict[str, Callable[..., Track]] = {
    "gpx": lambda path, date_hint=None: parse_gpx(path),
    "nmea": parse_nmea,
}

_EXT = {
    ".gpx": "gpx",
    ".nmea": "nmea",
    ".nma": "nmea",
    ".ubx_nmea": "nmea",
}
TRACK_EXTENSIONS = tuple(_EXT)  # extensions recognised without sniffing the content


def detect_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT:
        return _EXT[ext]
    with open(path, "rb") as fh:
        head = fh.read(8192).decode("ascii", errors="replace")
    if "<gpx" in head:
        return "gpx"
    if "$" in head and any(k in head for k in ("RMC,", "GGA,", "GNS,")):
        return "nmea"
    raise ValueError(f"Unrecognised track format: {os.path.basename(path)} (expected GPX or NMEA)")


def normalise(track: Track) -> Track:
    """Sort by time, drop duplicate timestamps (keep first)."""
    pts = sorted(track.points, key=lambda p: p.t)
    out = []
    dups = 0
    for p in pts:
        if out and abs(p.t - out[-1].t) < 1e-6:
            dups += 1
            continue
        out.append(p)
    if dups:
        track.warnings.append(f"Dropped {dups} duplicate timestamp(s)")
    track.points = out
    return track


def load_track(path: str, date_hint: Optional[date] = None) -> Track:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Track file not found: {path}")
    fmt = detect_format(path)
    track = READERS[fmt](path, date_hint=date_hint)
    return normalise(track)


__all__ = ["load_track", "detect_format", "READERS", "TRACK_EXTENSIONS"]
