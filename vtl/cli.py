"""Command line interface.

Examples:
  python main.py probe --video in.mp4
  python main.py track-info --track log.nmea [--date 2026-11-18]
  python main.py export --video in.mp4 --track log.gpx \
      --anchor-pts 12.345 --anchor-time "15:54:58.80" --tz +08:00 --rate 2 --out D:/out
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from typing import Optional

from . import __version__
from .align import from_metadata, from_single_anchor, from_two_anchors, scale_warning
from .export import run_export
from .models import Anchor, ExportConfig
from .timefmt import (
    fmt_iso_utc,
    fmt_local,
    format_tz,
    local_tz_minutes,
    parse_date,
    parse_tz,
    parse_user_time,
    to_local,
)
from .track import load_track
from .track.interp import TrackInterpolator, nominal_interval
from .video import open_video


def _date_hint(args) -> Optional[date]:
    return parse_date(args.date) if getattr(args, "date", None) else None


def cmd_probe(args) -> int:
    src = open_video(args.video)
    try:
        i = src.info
        print(f"File        : {i.path}")
        print(f"Backend     : {i.backend}")
        print(f"Video       : {i.describe()}")
        print(f"PTS range   : {i.start_pts:.4f} .. {i.end_pts:.4f}")
        ct = fmt_iso_utc(i.creation_time) if i.creation_time else "(none)"
        print(f"Created     : {ct}  (container metadata; reliability varies by camera)")
        pts, img = src.frame_at(i.start_pts)
        print(f"First frame : pts={pts:.4f} shape={img.shape}")
    finally:
        src.close()
    return 0


def cmd_track_info(args) -> int:
    tr = load_track(args.track, _date_hint(args))
    it = TrackInterpolator(tr, args.max_gap)
    tz = parse_tz(args.tz) if args.tz else local_tz_minutes()
    print(f"File     : {tr.path}")
    print(f"Format   : {tr.source}")
    print(f"Points   : {len(tr)}")
    print(f"Start    : {fmt_iso_utc(tr.start)}  ({fmt_local(tr.start, tz)} {format_tz(tz)})")
    print(f"End      : {fmt_iso_utc(tr.end)}  ({fmt_local(tr.end, tz)} {format_tz(tz)})")
    print(f"Interval : {nominal_interval(tr):.3f}s (median)")
    print(f"Gaps>{args.max_gap:g}s: {it.gap_count()}")
    for w in tr.warnings:
        print(f"Warning  : {w}")
    return 0


def cmd_export(args) -> int:
    tz = parse_tz(args.tz) if args.tz else local_tz_minutes()
    track = load_track(args.track, _date_hint(args)) if args.track else None
    src = open_video(args.video)
    try:
        info = src.info
        default_date = _date_hint(args)
        if default_date is None and track is not None:
            # entered clock times default to the track's date in the chosen timezone
            default_date = to_local(track.start, tz).date()

        alignment = None
        if args.anchor2_time and not args.anchor_time:
            raise ValueError("--anchor2-time needs --anchor-time (the first anchor)")
        if args.anchor_time:
            a1 = Anchor(args.anchor_pts, parse_user_time(args.anchor_time, tz, default_date))
            if args.anchor2_time:
                a2 = Anchor(args.anchor2_pts, parse_user_time(args.anchor2_time, tz, default_date))
                alignment = from_two_anchors(a1, a2)
                warn = scale_warning(alignment)
                if warn:
                    print("Warning:", warn, file=sys.stderr)
            else:
                alignment = from_single_anchor(a1)
        elif args.use_metadata:
            if info.creation_time is None:
                raise ValueError("Video has no creation_time metadata; use --anchor-time")
            alignment = from_metadata(info.creation_time, info.start_pts, args.offset)
        if alignment is not None and args.offset and not args.use_metadata:
            alignment = alignment.shifted(args.offset)
        if track is not None and alignment is None:
            raise ValueError("A track needs a time alignment: --anchor-time or --use-metadata")

        cfg = ExportConfig(
            rate=args.rate,
            distance_m=args.distance,
            start_offset=args.start_offset,
            in_pts=args.in_pts,
            out_pts=args.out_pts,
            max_gap_s=args.max_gap,
            min_move_m=args.min_move,
            image_format=args.format,
            jpeg_quality=args.quality,
            write_exif=not args.no_exif,
            tz_offset_min=tz,
            relative_date=(_date_hint(args) or datetime.now().date()).strftime("%Y%m%d"),
        )

        def progress(done: int, total: int, msg: str) -> None:
            print(f"\r{msg:<40}", end="", flush=True)

        res = run_export(src, track, alignment, cfg, args.out, progress)
        print()
        print(f"Job dir  : {res.job_dir}")
        print(f"Exported : {res.exported} / {res.total_slots} slots")
        for k, v in res.skipped.items():
            print(f"Skipped  : {k} = {v}")
    finally:
        src.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vtl", description="Video x GPX/NMEA time alignment (lite)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe", help="Show video metadata")
    sp.add_argument("--video", required=True)
    sp.set_defaults(func=cmd_probe)

    st = sub.add_parser("track-info", help="Summarise a GPX/NMEA track")
    st.add_argument("--track", required=True)
    st.add_argument("--date", help="Recording date for NMEA without RMC/ZDA (YYYY-MM-DD)")
    st.add_argument("--tz", help="Display timezone, e.g. +08:00 (default: system)")
    st.add_argument("--max-gap", type=float, default=3.0)
    st.set_defaults(func=cmd_track_info)

    se = sub.add_parser("export", help="Extract geotagged frames")
    se.add_argument("--video", required=True)
    se.add_argument("--track", help="GPX or NMEA file (optional)")
    se.add_argument("--out", required=True, help="Output root folder")
    se.add_argument("--anchor-pts", type=float, default=0.0, help="Anchor video time in seconds")
    se.add_argument("--anchor-time", help="Clock time at the anchor frame")
    se.add_argument("--anchor2-pts", type=float, default=0.0)
    se.add_argument("--anchor2-time", help="Second anchor time (drift correction)")
    se.add_argument("--use-metadata", action="store_true",
                    help="Use the video's creation_time as the first-frame time")
    se.add_argument("--offset", type=float, default=0.0, help="Extra time offset in seconds")
    se.add_argument("--tz", help="Timezone of entered times / filenames (default: system)")
    se.add_argument("--date", help="Date for times without a date / NMEA without date")
    se.add_argument("--rate", type=float, default=2.0, help="Images per second")
    se.add_argument("--distance", type=float, default=0.0,
                    help="One image every N metres travelled (needs --track; overrides --rate)")
    se.add_argument("--start-offset", type=float, default=0.0)
    se.add_argument("--in-pts", type=float)
    se.add_argument("--out-pts", type=float)
    se.add_argument("--max-gap", type=float, default=3.0)
    se.add_argument("--min-move", type=float, default=0.0)
    se.add_argument("--format", choices=["jpg", "png"], default="jpg")
    se.add_argument("--quality", type=int, default=95)
    se.add_argument("--no-exif", action="store_true")
    se.set_defaults(func=cmd_export)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:  # bad input, unreadable file, unwritable output
        print(f"Error: {exc}", file=sys.stderr)
        return 2
