"""Interpolation and geometry helpers over a Track."""
from __future__ import annotations

import math
from bisect import bisect_left
from typing import Optional

from ..models import GNSS_GAP, OK, OUTSIDE_COVERAGE, Fix, Track, TrackPoint

EARTH_R = 6371008.8


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _lerp(a: Optional[float], b: Optional[float], w: float) -> Optional[float]:
    if a is None or b is None:
        return a if b is None else (b if a is None else None)
    return a + (b - a) * w


def _lerp_angle(a: Optional[float], b: Optional[float], w: float) -> Optional[float]:
    if a is None or b is None:
        return a if a is not None else b
    d = ((b - a + 540.0) % 360.0) - 180.0
    return (a + d * w) % 360.0


class TrackInterpolator:
    def __init__(self, track: Track, max_gap_s: float = 3.0):
        self.track = track
        self.max_gap_s = max_gap_s
        self._times = [p.t for p in track.points]

    @property
    def start(self) -> float:
        return self._times[0]

    @property
    def end(self) -> float:
        return self._times[-1]

    def locate(self, t: float) -> tuple[str, Optional[Fix]]:
        """Return (status, fix) for UTC time ``t``."""
        times = self._times
        pts = self.track.points
        eps = 1e-6
        if not times or t < times[0] - eps or t > times[-1] + eps:
            return OUTSIDE_COVERAGE, None
        i = bisect_left(times, t)
        if i < len(times) and abs(times[i] - t) <= eps:
            p = pts[i]
            speed, course = p.speed, p.course
            if speed is None or course is None:
                ds, dc = self._derived(i)
                speed = speed if speed is not None else ds
                course = course if course is not None else dc
            return OK, Fix(t, p.lat, p.lon, p.alt, speed, course, p.fix, p.hdop, p.t, p.t)
        if i > 0 and abs(times[i - 1] - t) <= eps:
            return self.locate(times[i - 1])
        a, b = pts[i - 1], pts[i]
        if b.t - a.t > self.max_gap_s + eps or a.segment != b.segment:
            return GNSS_GAP, None
        w = (t - a.t) / (b.t - a.t)
        lat = a.lat + (b.lat - a.lat) * w
        lon = a.lon + (b.lon - a.lon) * w
        alt = _lerp(a.alt, b.alt, w)
        if a.speed is not None and b.speed is not None:
            speed = a.speed + (b.speed - a.speed) * w
        else:
            speed = haversine(a.lat, a.lon, b.lat, b.lon) / (b.t - a.t)
        if a.course is not None and b.course is not None:
            course = _lerp_angle(a.course, b.course, w)
        elif (a.lat, a.lon) != (b.lat, b.lon):
            course = bearing(a.lat, a.lon, b.lat, b.lon)
        else:
            course = None
        hdop = _lerp(a.hdop, b.hdop, w)
        return OK, Fix(t, lat, lon, alt, speed, course, a.fix, hdop, a.t, b.t)

    def _derived(self, i: int) -> tuple[Optional[float], Optional[float]]:
        pts = self.track.points
        j, k = (i - 1, i) if i > 0 else (i, i + 1)
        if k >= len(pts) or j < 0:
            return None, None
        a, b = pts[j], pts[k]
        if b.t - a.t > self.max_gap_s or b.t <= a.t:
            return None, None
        d = haversine(a.lat, a.lon, b.lat, b.lon)
        return d / (b.t - a.t), (bearing(a.lat, a.lon, b.lat, b.lon) if d > 0 else None)

    def speed_series(self) -> list[tuple[float, float]]:
        """(t, speed m/s) for each point; used by the UI speed curve."""
        out = []
        pts = self.track.points
        for i, p in enumerate(pts):
            s = p.speed
            if s is None:
                s, _ = self._derived(i)
            out.append((p.t, s if s is not None else 0.0))
        return out

    def gap_count(self) -> int:
        pts = self.track.points
        return sum(
            1
            for a, b in zip(pts, pts[1:])
            if b.t - a.t > self.max_gap_s or a.segment != b.segment
        )


def nominal_interval(track: Track) -> float:
    pts = track.points
    if len(pts) < 2:
        return 0.0
    diffs = sorted(b.t - a.t for a, b in zip(pts, pts[1:]))
    return diffs[len(diffs) // 2]


__all__ = ["TrackInterpolator", "haversine", "bearing", "nominal_interval", "TrackPoint"]
