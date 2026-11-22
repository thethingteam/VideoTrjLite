"""GPX 1.0 / 1.1 track parser (namespace agnostic)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

from ..models import Track, TrackPoint


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_iso8601(text: str) -> float:
    """Parse an ISO 8601 timestamp into UTC epoch seconds.

    Naive timestamps (no offset) are treated as UTC, as required by GPX.
    """
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    # Python < 3.11 fromisoformat is strict on fractional digits; normalise.
    if "." in s:
        head, rest = s.split(".", 1)
        digits = ""
        i = 0
        while i < len(rest) and rest[i].isdigit():
            digits += rest[i]
            i += 1
        tail = rest[i:]
        digits = (digits + "000000")[:6]
        s = f"{head}.{digits}{tail}"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


def _point_from(elem: ET.Element, segment: int) -> tuple[Optional[TrackPoint], str]:
    lat = _float(elem.get("lat"))
    lon = _float(elem.get("lon"))
    if lat is None or lon is None:
        return None, "missing lat/lon"
    t = alt = speed = course = hdop = None
    fix = None
    sats = None
    for child in elem.iter():
        if child is elem:
            continue
        name = _local(child.tag)
        text = child.text
        if text is None:
            continue
        if name == "time" and t is None:
            try:
                t = parse_iso8601(text)
            except ValueError:
                return None, "unparseable time"
        elif name == "ele":
            alt = _float(text)
        elif name == "speed":
            speed = _float(text)
        elif name in ("course", "heading"):
            course = _float(text)
        elif name == "hdop":
            hdop = _float(text)
        elif name == "fix":
            fix = text.strip()
        elif name == "sat":
            v = _float(text)
            sats = int(v) if v is not None else None
    if t is None:
        return None, "missing time"
    return TrackPoint(t, lat, lon, alt, speed, course, fix, hdop, sats, segment), ""


def parse_gpx(path: str) -> Track:
    warnings: list[str] = []
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"Not a valid GPX file (XML error: {exc})") from exc
    root = tree.getroot()
    if _local(root.tag) != "gpx":
        raise ValueError(f"Not a GPX file: root element is <{_local(root.tag)}>")

    points: list[TrackPoint] = []
    dropped: dict[str, int] = {}
    segment = 0

    def collect(container_name: str, point_name: str) -> None:
        nonlocal segment
        for cont in root.iter():
            if _local(cont.tag) != container_name:
                continue
            had = False
            for p in cont:
                if _local(p.tag) != point_name:
                    continue
                tp, why = _point_from(p, segment)
                if tp is None:
                    dropped[why] = dropped.get(why, 0) + 1
                    continue
                points.append(tp)
                had = True
            if had:
                segment += 1

    collect("trkseg", "trkpt")
    if not points:
        collect("rte", "rtept")
    if not points:
        # Last resort: timestamped waypoints.
        wpts = [p for p in root if _local(p.tag) == "wpt"]
        for p in wpts:
            tp, why = _point_from(p, 0)
            if tp is not None:
                points.append(tp)
    for why, n in dropped.items():
        warnings.append(f"Dropped {n} point(s): {why}")
    if not points:
        raise ValueError("GPX contains no timestamped track points")
    return Track(points=points, source="gpx", path=path, warnings=warnings)
