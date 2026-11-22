"""Shared data models.

Time conventions:
- Absolute times are UTC epoch seconds (float).
- Video times are presentation timestamps (PTS) in seconds, relative to the
  container's time base (the first frame is usually near 0 but not always).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class TrackPoint:
    t: float
    lat: float
    lon: float
    alt: Optional[float] = None
    speed: Optional[float] = None  # m/s
    course: Optional[float] = None  # degrees
    fix: Optional[str] = None
    hdop: Optional[float] = None
    sats: Optional[int] = None
    segment: int = 0


@dataclass
class Track:
    points: list[TrackPoint]
    source: str  # 'gpx' | 'nmea'
    path: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.points[0].t

    @property
    def end(self) -> float:
        return self.points[-1].t

    def __len__(self) -> int:
        return len(self.points)


@dataclass(frozen=True)
class VideoInfo:
    path: str
    duration: float
    start_pts: float
    end_pts: float
    avg_fps: float
    is_vfr: Optional[bool]  # None = unknown
    width: int
    height: int
    rotation: int  # clockwise degrees to apply for display: 0/90/180/270
    codec: str
    creation_time: Optional[float]  # UTC epoch, from container metadata
    backend: str

    @property
    def frame_duration(self) -> float:
        return 1.0 / self.avg_fps if self.avg_fps > 0 else 1.0 / 30.0

    def describe(self) -> str:
        vfr = {True: "VFR", False: "CFR", None: "VFR?"}[self.is_vfr]
        rot = f" rot{self.rotation}" if self.rotation else ""
        return (
            f"{self.width}x{self.height} {self.codec} {self.avg_fps:.3f}fps {vfr}{rot} "
            f"{self.duration:.2f}s [{self.backend}]"
        )


@dataclass(frozen=True)
class Anchor:
    pts: float
    utc: float


@dataclass(frozen=True)
class Alignment:
    """Linear mapping between video PTS and UTC.

    utc(pts) = anchor_utc + (pts - anchor_pts) * scale
    """

    anchor_pts: float
    anchor_utc: float
    scale: float = 1.0

    def to_utc(self, pts: float) -> float:
        return self.anchor_utc + (pts - self.anchor_pts) * self.scale

    def to_pts(self, utc: float) -> float:
        return self.anchor_pts + (utc - self.anchor_utc) / self.scale

    def shifted(self, seconds: float) -> "Alignment":
        return Alignment(self.anchor_pts, self.anchor_utc + seconds, self.scale)


@dataclass(frozen=True)
class Fix:
    """Interpolated position at an arbitrary time."""

    t: float
    lat: float
    lon: float
    alt: Optional[float]
    speed: Optional[float]
    course: Optional[float]
    fix: Optional[str]
    hdop: Optional[float]
    ref_pre: float
    ref_next: float


# Sample status values
OK = "ok"
OUTSIDE_COVERAGE = "outside_coverage"
GNSS_GAP = "gnss_gap"
BELOW_MIN_MOVEMENT = "below_min_movement"
FRAME_UNAVAILABLE = "frame_unavailable"
DUPLICATE_FRAME = "duplicate_frame"  # resolved to the frame of the previous image
NO_TRACK = "no_track"  # exported without GNSS (allowed mode)


@dataclass
class SampleSlot:
    index: int
    pts: float
    utc: Optional[float]
    status: str
    fix: Optional[Fix] = None
    reason: str = ""


@dataclass
class ExportConfig:
    rate: float = 2.0
    distance_m: float = 0.0  # > 0: one image every N metres travelled (ignores rate)
    start_offset: float = 0.0
    in_pts: Optional[float] = None
    out_pts: Optional[float] = None
    # Origin of the sampling grid. None = through the anchor (sync frame), so
    # the anchored frame is exported; the UI sets it to "Export from".
    grid_start: Optional[float] = None
    max_gap_s: float = 3.0
    min_move_m: float = 0.0
    image_format: str = "jpg"  # 'jpg' | 'png'
    jpeg_quality: int = 95
    write_exif: bool = True
    tz_offset_min: int = 0  # minutes east of UTC used for filenames/local_time
    relative_time: bool = False  # no absolute clock: filenames use elapsed time
    relative_date: str = ""  # YYYYMMDD used with relative_time


@dataclass
class ExportResult:
    job_dir: str
    exported: int
    skipped: dict[str, int]
    total_slots: int
    cancelled: bool = False
