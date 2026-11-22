"""Synthetic fixtures generated at test time (no binary files in the repo)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from fractions import Fraction
from typing import Optional

import av
import numpy as np

# Base track: 2026-11-18 07:00:00 UTC, moving north-east at ~5 m/s, 1 Hz.
T0 = datetime(2026, 11, 18, 7, 0, 0, tzinfo=timezone.utc).timestamp()
LAT0, LON0 = 25.0, 121.5
DLAT = 5.0 / 111_320.0  # deg/s (~5 m/s northward)
DLON = 0.0


def frame_value(pts: float) -> int:
    """Code encoded in a frame displayed at ``pts`` (10 codes / second, cycles every 6 s)."""
    return int(round(pts * 10)) % 60


def _gray(code: int) -> int:
    # 4 gray levels per code so YUV<->RGB rounding (+-1) can't change the code
    return 8 + code * 4


def decode_value(img: np.ndarray) -> int:
    return int(round((float(img[8:-8, 8:-8].mean()) - 8) / 4))


def make_video(
    path: str,
    seconds: float = 6.0,
    fps: int = 30,
    rotation: int = 0,
    vfr_pattern: Optional[list[float]] = None,
    size: tuple[int, int] = (64, 48),
    codec: str = "libx264",
    creation_time: Optional[str] = None,
) -> list[float]:
    """Write a video whose frame brightness encodes its timestamp.

    ``vfr_pattern``: list of frame durations (seconds) cycled to create VFR.
    Returns the list of frame PTS (seconds).
    """
    out = av.open(path, "w")
    if creation_time:
        out.metadata["creation_time"] = creation_time
    tb = Fraction(1, 9000)
    try:
        st = out.add_stream(codec, rate=fps)
    except Exception:
        st = out.add_stream("mpeg4", rate=fps)
    st.width, st.height = size
    st.pix_fmt = "yuv420p"
    if st.codec_context.name != "libx264":
        tb = Fraction(1, fps)  # e.g. MPEG-2 only accepts standard frame-rate time bases
    st.time_base = tb
    st.codec_context.time_base = tb
    if st.codec_context.name == "libx264":
        st.options = {"crf": "0", "preset": "ultrafast", "bf": "0"}
    else:
        st.bit_rate = 8_000_000
        st.codec_context.gop_size = 12
    if rotation:
        st.set_display_rotation(-rotation)  # FFmpeg uses CCW angles
    pts_list: list[float] = []
    t = 0.0
    k = 0
    while t < seconds - 1e-9:
        v = _gray(frame_value(t))
        arr = np.full((size[1], size[0], 3), v, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        frame.pts = int(round(t / tb))
        frame.time_base = tb
        for pkt in st.encode(frame):
            out.mux(pkt)
        pts_list.append(t)
        dur = vfr_pattern[k % len(vfr_pattern)] if vfr_pattern else 1.0 / fps
        t += dur
        k += 1
    for pkt in st.encode():
        out.mux(pkt)
    out.close()
    return pts_list


def track_points(seconds: int = 60, start_offset: float = 0.0, gap: Optional[tuple[int, int]] = None):
    """Yield (t, lat, lon, alt) at 1 Hz; ``gap`` = (from_s, to_s) omitted seconds."""
    for s in range(seconds + 1):
        if gap and gap[0] < s < gap[1]:
            continue
        t = T0 + start_offset + s
        yield t, LAT0 + DLAT * s, LON0 + DLON * s, 10.0 + s * 0.1


def write_gpx(path: str, **kw) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">',
        "<trk><trkseg>",
    ]
    for t, lat, lon, alt in track_points(**kw):
        ts = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        lines.append(f'<trkpt lat="{lat:.9f}" lon="{lon:.9f}"><ele>{alt:.2f}</ele><time>{ts}</time></trkpt>')
    lines += ["</trkseg></trk>", "</gpx>"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _nmea_coord(v: float, is_lat: bool) -> tuple[str, str]:
    hemi = ("N" if v >= 0 else "S") if is_lat else ("E" if v >= 0 else "W")
    v = abs(v)
    d = int(v)
    m = (v - d) * 60
    return (f"{d:02d}{m:09.6f}" if is_lat else f"{d:03d}{m:09.6f}"), hemi


def nmea_sentence(body: str) -> str:
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}"


def write_nmea(path: str, talker: str = "GN", with_rmc: bool = True, **kw) -> None:
    out = []
    for t, lat, lon, alt in track_points(**kw):
        dt = datetime.fromtimestamp(t, timezone.utc)
        hms = dt.strftime("%H%M%S") + f".{dt.microsecond // 10000:02d}"
        la, lah = _nmea_coord(lat, True)
        lo, loh = _nmea_coord(lon, False)
        out.append(nmea_sentence(f"{talker}GGA,{hms},{la},{lah},{lo},{loh},1,12,0.8,{alt:.2f},M,0.0,M,,"))
        if with_rmc:
            ddmmyy = dt.strftime("%d%m%y")
            out.append(nmea_sentence(f"{talker}RMC,{hms},A,{la},{lah},{lo},{loh},9.72,0.0,{ddmmyy},,,A"))
    with open(path, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")


def expected_lat(t: float) -> float:
    return LAT0 + DLAT * (t - T0)


__all__ = ["make_video", "write_gpx", "write_nmea", "nmea_sentence", "T0", "math"]
