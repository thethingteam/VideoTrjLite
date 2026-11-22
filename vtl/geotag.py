"""EXIF GPS / time tags (piexif)."""
from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
from typing import Optional

import piexif

from .models import Fix
from .timefmt import format_tz, to_local


def _rat(value: float, max_den: int = 1_000_000) -> tuple[int, int]:
    f = Fraction(value).limit_denominator(max_den)
    return f.numerator, f.denominator


def _dms(deg: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    deg = abs(deg)
    d = int(deg)
    m_full = (deg - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    return (d, 1), (m, 1), (int(round(s * 10000)), 10000)


def build_exif(
    fix: Optional[Fix],
    utc: Optional[float],
    tz_minutes: int,
    software: str = "video_trj_integration_lite",
) -> bytes:
    zeroth = {piexif.ImageIFD.Software: software.encode()}
    exif: dict = {}
    gps: dict = {}
    if utc is not None:
        local = to_local(utc, tz_minutes)
        exif[piexif.ExifIFD.DateTimeOriginal] = local.strftime("%Y:%m:%d %H:%M:%S").encode()
        exif[piexif.ExifIFD.SubSecTimeOriginal] = f"{local.microsecond // 1000:03d}".encode()
        exif[piexif.ExifIFD.OffsetTimeOriginal] = format_tz(tz_minutes)[3:].encode()
        zeroth[piexif.ImageIFD.DateTime] = exif[piexif.ExifIFD.DateTimeOriginal]
    if fix is not None:
        gps[piexif.GPSIFD.GPSVersionID] = (2, 3, 0, 0)
        gps[piexif.GPSIFD.GPSLatitudeRef] = b"N" if fix.lat >= 0 else b"S"
        gps[piexif.GPSIFD.GPSLatitude] = _dms(fix.lat)
        gps[piexif.GPSIFD.GPSLongitudeRef] = b"E" if fix.lon >= 0 else b"W"
        gps[piexif.GPSIFD.GPSLongitude] = _dms(fix.lon)
        if fix.alt is not None:
            gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if fix.alt >= 0 else 1
            gps[piexif.GPSIFD.GPSAltitude] = _rat(abs(fix.alt), 1000)
        if fix.speed is not None:
            gps[piexif.GPSIFD.GPSSpeedRef] = b"K"
            gps[piexif.GPSIFD.GPSSpeed] = _rat(fix.speed * 3.6, 1000)
        if fix.course is not None:
            gps[piexif.GPSIFD.GPSTrackRef] = b"T"
            gps[piexif.GPSIFD.GPSTrack] = _rat(fix.course, 100)
        if fix.hdop is not None:
            gps[piexif.GPSIFD.GPSDOP] = _rat(fix.hdop, 100)
        gps[piexif.GPSIFD.GPSMapDatum] = b"WGS-84"
        dt = datetime.fromtimestamp(fix.t, timezone.utc)
        gps[piexif.GPSIFD.GPSDateStamp] = dt.strftime("%Y:%m:%d").encode()
        sec = dt.second + dt.microsecond / 1e6
        gps[piexif.GPSIFD.GPSTimeStamp] = ((dt.hour, 1), (dt.minute, 1), _rat(sec, 1000))
    return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps, "1st": {}, "thumbnail": None})
