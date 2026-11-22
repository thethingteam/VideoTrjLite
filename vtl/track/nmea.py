"""NMEA 0183 parser.

Supported sentences (any talker ID: GP, GN, GL, GA, BD, GB, QZ ...):
- RMC: date, time, position, speed, course, status
- GGA: time, position, quality, satellites, HDOP, altitude
- GNS: time, position, mode, satellites, HDOP, altitude
- ZDA: date (used when RMC is absent)

Sentences belonging to the same epoch (same time-of-day) are merged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from ..models import Track, TrackPoint

KNOT_MPS = 0.514444
_SENTENCE = re.compile(r"\$([A-Z]{2})([A-Z]{3}),([^*\r\n]*)(?:\*([0-9A-Fa-f]{2}))?")


def _checksum_ok(body: str, given: Optional[str]) -> bool:
    if given is None:
        return True  # some loggers omit the checksum
    c = 0
    for ch in body:
        c ^= ord(ch)
    return c == int(given, 16)


def _coord(value: str, hemi: str) -> Optional[float]:
    if not value or not hemi:
        return None
    try:
        v = float(value)
    except ValueError:
        return None
    deg = int(v // 100)
    minutes = v - deg * 100
    out = deg + minutes / 60.0
    if hemi.upper() in ("S", "W"):
        out = -out
    return out


def _tod(value: str) -> Optional[float]:
    """hhmmss(.sss) -> seconds of day."""
    if not value or len(value) < 6:
        return None
    try:
        h = int(value[0:2])
        m = int(value[2:4])
        s = float(value[4:])
    except ValueError:
        return None
    if h > 23 or m > 59 or s >= 61:
        return None
    return h * 3600 + m * 60 + s


def _f(value: str) -> Optional[float]:
    try:
        return float(value) if value != "" else None
    except ValueError:
        return None


@dataclass
class _Epoch:
    tod: float
    date: Optional[date] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    speed: Optional[float] = None
    course: Optional[float] = None
    fix: Optional[str] = None
    hdop: Optional[float] = None
    sats: Optional[int] = None
    valid: bool = True
    has_pos_sentence: bool = False


def _parse_date(ddmmyy: str) -> Optional[date]:
    if len(ddmmyy) != 6 or not ddmmyy.isdigit():
        return None
    d, m, y = int(ddmmyy[0:2]), int(ddmmyy[2:4]), int(ddmmyy[4:6])
    y += 2000 if y < 80 else 1900
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_nmea(path: str, date_hint: Optional[date] = None) -> Track:
    warnings: list[str] = []
    bad_checksum = 0
    epochs: list[_Epoch] = []
    by_key: dict[tuple[int, float], _Epoch] = {}
    # Each time the time-of-day goes backwards a lot we bump a "day counter"
    # so identical TODs on different days don't merge.
    day_index = 0
    last_tod: Optional[float] = None
    zda_dates: list[tuple[int, float, date]] = []

    def epoch_for(tod: float) -> _Epoch:
        nonlocal day_index, last_tod
        if last_tod is not None and tod < last_tod - 12 * 3600:
            day_index += 1
        last_tod = tod
        key = (day_index, round(tod, 3))
        ep = by_key.get(key)
        if ep is None:
            ep = _Epoch(tod=tod)
            ep.day_index = day_index  # type: ignore[attr-defined]
            by_key[key] = ep
            epochs.append(ep)
        return ep

    with open(path, "r", encoding="ascii", errors="replace") as fh:
        for line in fh:
            idx = line.find("$")
            if idx < 0:
                continue
            m = _SENTENCE.search(line, idx)
            if not m:
                continue
            talker, kind, data, cks = m.groups()
            body = f"{talker}{kind},{data}"
            if not _checksum_ok(body, cks):
                bad_checksum += 1
                continue
            f = data.split(",")
            try:
                if kind == "RMC" and len(f) >= 9:
                    tod = _tod(f[0])
                    if tod is None:
                        continue
                    ep = epoch_for(tod)
                    ep.has_pos_sentence = True
                    if f[1].upper() != "A":
                        ep.valid = False
                        continue
                    ep.lat = _coord(f[2], f[3]) if ep.lat is None else ep.lat
                    ep.lon = _coord(f[4], f[5]) if ep.lon is None else ep.lon
                    spd = _f(f[6])
                    ep.speed = spd * KNOT_MPS if spd is not None else ep.speed
                    ep.course = _f(f[7]) if _f(f[7]) is not None else ep.course
                    d = _parse_date(f[8])
                    if d:
                        ep.date = d
                    if ep.fix is None:
                        ep.fix = "A"
                elif kind in ("GGA", "GNS") and len(f) >= 9:
                    tod = _tod(f[0])
                    if tod is None:
                        continue
                    ep = epoch_for(tod)
                    ep.has_pos_sentence = True
                    if kind == "GGA":
                        quality = f[5]
                        if quality in ("", "0"):
                            ep.valid = False
                            continue
                        ep.fix = quality
                    else:
                        mode = f[5]
                        if not mode or set(mode) <= {"N"}:
                            ep.valid = False
                            continue
                        ep.fix = mode
                    lat = _coord(f[1], f[2])
                    lon = _coord(f[3], f[4])
                    if lat is not None and lon is not None:
                        ep.lat, ep.lon = lat, lon
                    sats = _f(f[6])
                    ep.sats = int(sats) if sats is not None else ep.sats
                    ep.hdop = _f(f[7])
                    ep.alt = _f(f[8])
                elif kind == "ZDA" and len(f) >= 4:
                    tod = _tod(f[0])
                    try:
                        d = date(int(f[3]), int(f[2]), int(f[1]))
                    except ValueError:
                        continue
                    if tod is not None:
                        zda_dates.append((day_index, tod, d))
            except (ValueError, IndexError):
                continue

    if bad_checksum:
        warnings.append(f"Skipped {bad_checksum} sentence(s) with bad checksum")

    # Resolve a date for each day_index.
    day_dates: dict[int, date] = {}
    for ep in epochs:
        di = ep.day_index  # type: ignore[attr-defined]
        if ep.date is not None and di not in day_dates:
            day_dates[di] = ep.date
    for di, _tod_, d in zda_dates:
        day_dates.setdefault(di, d)
    if not day_dates:
        if date_hint is None:
            raise ValueError(
                "NMEA file has no date (no RMC/ZDA sentences). Please provide the recording date."
            )
        day_dates[0] = date_hint
    # Fill missing day indices by stepping from a known one.
    max_di = max((ep.day_index for ep in epochs), default=0)  # type: ignore[attr-defined]
    known = sorted(day_dates)
    for di in range(max_di + 1):
        if di in day_dates:
            continue
        ref = min(known, key=lambda k: abs(k - di))
        day_dates[di] = day_dates[ref] + timedelta(days=di - ref)

    points: list[TrackPoint] = []
    invalid = 0
    for ep in epochs:
        if not ep.valid or ep.lat is None or ep.lon is None:
            if ep.has_pos_sentence:
                invalid += 1
            continue
        di = ep.day_index  # type: ignore[attr-defined]
        # Prefer the epoch's own RMC date (handles midnight rollover inside RMC)
        d = ep.date or day_dates[di]
        base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
        points.append(
            TrackPoint(
                t=base + ep.tod,
                lat=ep.lat,
                lon=ep.lon,
                alt=ep.alt,
                speed=ep.speed,
                course=ep.course,
                fix=ep.fix,
                hdop=ep.hdop,
                sats=ep.sats,
                segment=0,
            )
        )
    if invalid:
        warnings.append(f"Skipped {invalid} epoch(s) without a valid fix")
    if not points:
        raise ValueError("NMEA file contains no valid position fixes")
    return Track(points=points, source="nmea", path=path, warnings=warnings)
