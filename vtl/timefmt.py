"""User-facing time parsing and formatting.

Accepted user inputs (interpreted in the given timezone unless an explicit
offset is present):
- ``15:54:58.80`` / ``15:54:58`` / ``15:54``  (date taken from ``default_date``)
- ``下午 03:54:58.80`` / ``上午 9:01:02`` / ``03:54:58 PM``
- ``2026-11-18 15:54:58.8`` / ``2026/11/18 15:54:58``
- ISO 8601 with offset: ``2026-11-18T07:54:58.8Z``, ``...+08:00``
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

_TZ_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d+)?))?$")
_DATE_RE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")


def parse_tz(text: str) -> int:
    """'UTC+08:00' / '+8' / '-0530' / 'UTC' -> minutes east of UTC."""
    s = text.strip()
    if s.upper() in ("", "UTC", "GMT", "Z"):
        return 0
    m = _TZ_RE.match(s)
    if not m:
        raise ValueError(f"Invalid timezone {text!r}; use e.g. UTC+08:00")
    sign, hh, mm = m.groups()
    minutes = int(hh) * 60 + int(mm or 0)
    if minutes > 14 * 60:
        raise ValueError(f"Timezone offset out of range: {text!r}")
    return minutes if sign == "+" else -minutes


def format_tz(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "-"
    m = abs(minutes)
    return f"UTC{sign}{m // 60:02d}:{m % 60:02d}"


def local_tz_minutes() -> int:
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def parse_date(text: str) -> date:
    s = text.strip()
    if re.fullmatch(r"\d{8}", s):
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    m = _DATE_RE.match(s)
    if not m:
        raise ValueError(f"Invalid date {text!r}; use YYYY-MM-DD or YYYYMMDD")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _parse_clock(text: str) -> tuple[int, int, float]:
    s = text.strip()
    ampm = None
    for token, val in (("上午", "AM"), ("下午", "PM"), ("AM", "AM"), ("PM", "PM"), ("am", "AM"), ("pm", "PM")):
        if s.startswith(token):
            ampm, s = val, s[len(token):].strip()
            break
        if s.endswith(token):
            ampm, s = val, s[: -len(token)].strip()
            break
    m = _TIME_RE.match(s)
    if not m:
        raise ValueError(f"Invalid time {text!r}; use HH:MM:SS(.ss)")
    h, mi = int(m.group(1)), int(m.group(2))
    sec = float(m.group(3)) if m.group(3) else 0.0
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError(f"Invalid 12-hour time {text!r}")
        h = h % 12 + (12 if ampm == "PM" else 0)
    if h > 23 or mi > 59 or sec >= 60:
        raise ValueError(f"Invalid time {text!r}")
    return h, mi, sec


def parse_user_time(text: str, tz_minutes: int, default_date: Optional[date]) -> float:
    """Parse a user-entered timestamp into UTC epoch seconds."""
    s = text.strip()
    if not s:
        raise ValueError("Time is empty")
    # ISO 8601 with explicit offset or Z
    if "T" in s or s.endswith(("Z", "z")) or re.search(r"[+-]\d{2}:?\d{2}$", s):
        from .track.gpx import parse_iso8601

        try:
            if re.search(r"[+-]\d{2}:?\d{2}$|[Zz]$", s):
                return parse_iso8601(s.replace(" ", "T", 1))
            # 'T' but naive: interpret in tz
            s = s.replace("T", " ", 1)
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp {text!r}") from exc
    parts = s.split()
    d: Optional[date] = None
    if parts and _DATE_RE.match(parts[0]):
        d = parse_date(parts[0])
        s = " ".join(parts[1:])
    if d is None:
        d = default_date
    if d is None:
        raise ValueError("No date available: include a date (YYYY-MM-DD HH:MM:SS) or load a track")
    h, mi, sec = _parse_clock(s)
    tz = timezone(timedelta(minutes=tz_minutes))
    whole = int(sec)
    micro = int(round((sec - whole) * 1e6))
    dt = datetime(d.year, d.month, d.day, h, mi, whole, tzinfo=tz) + timedelta(microseconds=micro)
    return dt.timestamp()


def to_local(utc: float, tz_minutes: int) -> datetime:
    return datetime.fromtimestamp(utc, timezone(timedelta(minutes=tz_minutes)))


def fmt_local(utc: float, tz_minutes: int, with_date: bool = True) -> str:
    dt = to_local(utc, tz_minutes)
    ms = int(round(dt.microsecond / 1000))
    if ms == 1000:
        dt = dt.replace(microsecond=0) + timedelta(seconds=1)
        ms = 0
    base = dt.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")
    return f"{base}.{ms:03d}"


def fmt_iso_utc(utc: float) -> str:
    dt = datetime.fromtimestamp(utc, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def filename_stamp(utc: float, tz_minutes: int) -> str:
    """YYYYMMDD_HHMMSSmmm in the given timezone (millisecond resolution)."""
    ms_total = int(round(utc * 1000))
    dt = datetime.fromtimestamp(ms_total // 1000, timezone(timedelta(minutes=tz_minutes)))
    return dt.strftime("%Y%m%d_%H%M%S") + f"{ms_total % 1000:03d}"


def relative_stamp(seconds: float, yyyymmdd: str) -> str:
    ms_total = int(round(max(0.0, seconds) * 1000))
    s, ms = divmod(ms_total, 1000)
    h, rem = divmod(s, 3600)
    mi, sec = divmod(rem, 60)
    return f"{yyyymmdd}_{h:02d}{mi:02d}{sec:02d}{ms:03d}"


def parse_offset(text: str) -> float:
    """'-12.5' / '+8:00:00' / '-1:02.5' / '' -> seconds."""
    s = text.strip().replace(" ", "")
    if not s:
        return 0.0
    sign = -1.0 if s.startswith("-") else 1.0
    body = s.lstrip("+-")
    try:
        if ":" in body:
            total = 0.0
            for part in body.split(":"):
                total = total * 60 + float(part)
        else:
            total = float(body)
    except ValueError as exc:
        raise ValueError(f"Invalid offset {text!r}; use seconds (-12.5) or h:mm:ss (+8:00:00)") from exc
    return sign * total


def fmt_offset(seconds: float) -> str:
    if abs(seconds) < 600:
        return f"{seconds:+.3f}".rstrip("0").rstrip(".")
    sign = "-" if seconds < 0 else "+"
    ms_total = int(round(abs(seconds) * 1000))  # integer maths: no 59.9996 -> "60.000"
    s, ms = divmod(ms_total, 1000)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    frac = f".{ms:03d}".rstrip("0").rstrip(".")
    return f"{sign}{h}:{m:02d}:{sec:02d}{frac}"


def fmt_pts(pts: float) -> str:
    sign = "-" if pts < 0 else ""
    pts = abs(pts)
    m, s = divmod(pts, 60)
    h, m = divmod(int(m), 60)
    return f"{sign}{h:d}:{m:02d}:{s:06.3f}"
