"""Build the export schedule: which video timestamps become images."""
from __future__ import annotations

import math
from typing import Optional

from .models import (
    BELOW_MIN_MOVEMENT,
    NO_TRACK,
    OK,
    Alignment,
    ExportConfig,
    SampleSlot,
    VideoInfo,
)
from .track.interp import TrackInterpolator, haversine


def export_range(
    info: VideoInfo,
    alignment: Optional[Alignment],
    interp: Optional[TrackInterpolator],
    cfg: ExportConfig,
) -> tuple[float, float]:
    """Return the [lo, hi] PTS range eligible for export."""
    lo = info.start_pts
    hi = info.end_pts - 0.5 * info.frame_duration
    if interp is not None and alignment is not None:
        t_lo = alignment.to_pts(interp.start)
        t_hi = alignment.to_pts(interp.end)
        lo, hi = max(lo, t_lo), min(hi, t_hi)
        if hi < lo:
            raise ValueError(
                "The track does not overlap the video with the current alignment. "
                "Check the anchor time, date and timezone."
            )
    if cfg.in_pts is not None:
        lo = max(lo, cfg.in_pts)
    if cfg.out_pts is not None:
        hi = min(hi, cfg.out_pts)
    lo += cfg.start_offset
    if hi < lo:
        raise ValueError("Export range is empty (check start offset / in-out points)")
    return lo, hi


def build_schedule(
    info: VideoInfo,
    alignment: Optional[Alignment],
    interp: Optional[TrackInterpolator],
    cfg: ExportConfig,
) -> list[SampleSlot]:
    if not (math.isfinite(cfg.rate) and math.isfinite(cfg.distance_m)):
        raise ValueError("Export rate and distance must be finite numbers")
    if cfg.distance_m < 0:
        raise ValueError("Distance must not be negative")
    if cfg.distance_m == 0 and cfg.rate <= 0:
        raise ValueError("Export rate must be greater than 0")
    if interp is not None and alignment is None:
        raise ValueError("A time alignment is required when a track is loaded")
    if cfg.distance_m > 0 and interp is None:
        raise ValueError("Exporting by distance needs a GPS track")
    lo, hi = export_range(info, alignment, interp, cfg)

    # The grid passes through the anchor (sync frame) so the anchored frame is
    # exported, unless an explicit origin is given (the UI's "Export from").
    if cfg.grid_start is not None:
        grid0 = cfg.grid_start
    else:
        grid0 = alignment.anchor_pts if alignment is not None else info.start_pts
    if cfg.distance_m > 0:
        # candidates at (about) every video frame; keep one per distance step
        step = max(info.frame_duration, 1.0 / 60.0)
    else:
        step = 1.0 / cfg.rate
    eps = 1e-9
    n0 = math.ceil((lo - grid0) / step - eps)
    n1 = math.floor((hi - grid0) / step + eps)
    if cfg.distance_m > 0:
        assert alignment is not None and interp is not None
        return _distance_schedule(grid0, step, n0, n1, alignment, interp, cfg.distance_m)

    slots: list[SampleSlot] = []
    last_kept = None  # (lat, lon) of the last OK slot in the current run
    for idx, n in enumerate(range(n0, n1 + 1)):
        pts = grid0 + n * step
        utc = alignment.to_utc(pts) if alignment is not None else None
        if interp is None:
            slots.append(SampleSlot(idx, pts, utc, NO_TRACK if utc is None else OK))
            continue
        status, fix = interp.locate(utc)  # type: ignore[arg-type]
        if status != OK:
            slots.append(SampleSlot(idx, pts, utc, status))
            last_kept = None  # reset movement reference after a gap
            continue
        assert fix is not None
        if cfg.min_move_m > 0 and last_kept is not None:
            d = haversine(last_kept[0], last_kept[1], fix.lat, fix.lon)
            if d < cfg.min_move_m:
                slots.append(
                    SampleSlot(idx, pts, utc, BELOW_MIN_MOVEMENT, fix, f"moved {d:.2f} m")
                )
                continue
        last_kept = (fix.lat, fix.lon)
        slots.append(SampleSlot(idx, pts, utc, OK, fix))
    return slots


def _distance_schedule(
    grid0: float,
    step: float,
    n0: int,
    n1: int,
    alignment: Alignment,
    interp: TrackInterpolator,
    distance_m: float,
) -> list[SampleSlot]:
    """One slot each time the distance travelled along the track reaches ``distance_m``.

    Distance is accumulated between consecutive candidate frames, so the
    spacing follows the path (not the straight line). The first usable frame
    and the first frame after a GPS gap are always kept. Only kept slots and
    the first slot of each unusable run are returned (so skipped.csv shows
    where gaps are without listing every frame).
    """
    slots: list[SampleSlot] = []
    prev = None  # (lat, lon) of the previous usable candidate
    acc = 0.0
    in_bad = False
    for n in range(n0, n1 + 1):
        pts = grid0 + n * step
        utc = alignment.to_utc(pts)
        status, fix = interp.locate(utc)
        if status != OK or fix is None:
            if not in_bad:
                slots.append(SampleSlot(len(slots), pts, utc, status))
            in_bad = True
            prev = None  # restart after a gap
            continue
        in_bad = False
        keep = prev is None
        if prev is not None:
            acc += haversine(prev[0], prev[1], fix.lat, fix.lon)
            if acc >= distance_m:
                keep = True
                acc = (acc - distance_m) % distance_m
        else:
            acc = 0.0
        prev = (fix.lat, fix.lon)
        if keep:
            slots.append(SampleSlot(len(slots), pts, utc, OK, fix))
    return slots


def exportable(slot: SampleSlot) -> bool:
    return slot.status in (OK, NO_TRACK)
