import csv
import json
import os
import tempfile
import threading
import unittest

import piexif
from fixtures import DLAT, LAT0, T0, decode_value, frame_value, make_video, write_gpx, write_nmea
from PIL import Image

from vtl.export import run_export
from vtl.models import BELOW_MIN_MOVEMENT, GNSS_GAP, NO_TRACK, OK, Alignment, ExportConfig
from vtl.sampler import build_schedule
from vtl.track import load_track
from vtl.track.interp import TrackInterpolator
from vtl.video import open_video


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def dms_to_deg(dms):
    (d, dd), (m, md), (s, sd) = dms
    return d / dd + m / md / 60 + s / sd / 3600


class SamplerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        p = os.path.join(cls.tmp.name, "t.gpx")
        write_gpx(p, seconds=60, gap=(20, 25))
        cls.track = load_track(p)
        from vtl.models import VideoInfo

        cls.info = VideoInfo("v", 30.0, 0.0, 30.0, 30.0, False, 64, 48, 0, "h264", None, "test")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_grid_passes_through_anchor(self):
        al = Alignment(1.3, T0 + 5)  # video 1.3s == track 5s
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=2))
        pts = [s.pts for s in slots]
        self.assertTrue(any(abs(p - 1.3) < 1e-9 for p in pts))
        self.assertAlmostEqual(pts[0], 0.3)
        self.assertAlmostEqual(pts[1] - pts[0], 0.5)

    def test_grid_starts_at_export_from(self):
        al = Alignment(1.3, T0 + 5)  # sync frame 1.3 s
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=2, in_pts=4.12, grid_start=4.12))
        self.assertAlmostEqual(slots[0].pts, 4.12)
        self.assertAlmostEqual(slots[1].pts, 4.62)
        self.assertAlmostEqual(slots[0].utc, T0 + 5 + (4.12 - 1.3))

    def test_in_pts_alone_keeps_anchor_grid(self):
        # CLI --in-pts only clips; the grid still passes through the anchor
        al = Alignment(1.3, T0 + 5)
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=2, in_pts=4.12))
        self.assertAlmostEqual(slots[0].pts, 4.3)

    def test_clip_to_track_and_gap(self):
        al = Alignment(0.0, T0 + 10)  # video [0,30] -> track [10,40]
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=1))
        gap = [s for s in slots if s.status == GNSS_GAP]
        self.assertEqual([round(s.utc - T0) for s in gap], [21, 22, 23, 24])
        self.assertTrue(all(s.status == OK for s in slots if s not in gap))

    def test_no_overlap_raises(self):
        al = Alignment(0.0, T0 + 1000)
        with self.assertRaises(ValueError):
            build_schedule(self.info, al, TrackInterpolator(self.track), ExportConfig())

    def test_min_movement(self):
        al = Alignment(0.0, T0)
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=4, min_move_m=4.0))
        ok = [s for s in slots if s.status == OK]
        below = [s for s in slots if s.status == BELOW_MIN_MOVEMENT]
        self.assertTrue(below)
        # ~5 m/s at 4 Hz -> keep every 4th slot (moved 5 m)
        self.assertAlmostEqual(ok[1].pts - ok[0].pts, 1.0, places=6)

    def test_by_distance(self):
        from vtl.track.interp import haversine

        al = Alignment(0.0, T0 + 10)  # video [0,30] -> track [10,40], gap 20..25
        it = TrackInterpolator(self.track)
        slots = build_schedule(self.info, al, it, ExportConfig(rate=99, distance_m=10.0))
        ok = [s for s in slots if s.status == OK]
        gaps = [s for s in slots if s.status == GNSS_GAP]
        self.assertEqual(len(gaps), 1)  # one marker per gap, not one per frame
        before = [s for s in ok if s.utc < gaps[0].utc]
        after = [s for s in ok if s.utc > gaps[0].utc]
        # ~5 m/s -> one image every ~2 s, spaced ~10 m
        for run in (before, after):
            for a, b in zip(run, run[1:]):
                d = haversine(a.fix.lat, a.fix.lon, b.fix.lat, b.fix.lon)
                self.assertAlmostEqual(d, 10.0, delta=0.25)
        self.assertAlmostEqual(before[0].utc, T0 + 10, places=6)
        self.assertAlmostEqual(after[0].utc, T0 + 25, delta=0.05)  # restart after the gap
        # 10..20 s = just under 50 m (haversine radius) -> 0, 10, .., 40 m
        self.assertEqual(len(before), 5)
        with self.assertRaises(ValueError):
            build_schedule(self.info, None, None, ExportConfig(distance_m=5.0))

    def test_no_track(self):
        slots = build_schedule(self.info, None, None, ExportConfig(rate=1))
        self.assertEqual(len(slots), 30)
        self.assertTrue(all(s.status == NO_TRACK for s in slots))


class ExportEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.video = os.path.join(d, "v.mp4")
        make_video(cls.video, seconds=6.0, fps=30)
        cls.vfr = os.path.join(d, "vfr.mkv")
        make_video(cls.vfr, seconds=6.0, fps=30, vfr_pattern=[1 / 30, 1 / 15, 1 / 60, 1 / 30], rotation=90)
        cls.gpx = os.path.join(d, "t.gpx")
        write_gpx(cls.gpx, seconds=60)
        cls.nmea = os.path.join(d, "t.nmea")
        write_nmea(cls.nmea, seconds=60)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, video, track_path, alignment, **cfg_kw):
        out = os.path.join(self.tmp.name, "out")
        src = open_video(video)
        try:
            tr = load_track(track_path) if track_path else None
            cfg = ExportConfig(tz_offset_min=480, **cfg_kw)
            return run_export(src, tr, alignment, cfg, out)
        finally:
            src.close()

    def test_gpx_export(self):
        al = Alignment(2.0, T0 + 10.0)  # video 2.0s == track second 10
        res = self._run(self.video, self.gpx, al, rate=2)
        self.assertEqual(res.exported, 12)
        rows = read_csv(os.path.join(res.job_dir, "frames.csv"))
        self.assertEqual(len(rows), 12)
        r = next(r for r in rows if abs(float(r["video_pts"]) - 2.0) < 1e-3)
        self.assertEqual(r["image_file"], "20261118_150010000.jpg")
        self.assertAlmostEqual(float(r["latitude"]), LAT0 + 10 * DLAT, places=7)
        r = next(r for r in rows if abs(float(r["video_pts"]) - 3.5) < 1e-3)
        self.assertAlmostEqual(float(r["latitude"]), LAT0 + 11.5 * DLAT, places=7)
        self.assertEqual(r["utc_time"], "2026-11-18T07:00:11.500Z")
        # frame content matches the requested timestamp
        img = Image.open(os.path.join(res.job_dir, "images", r["image_file"]))
        import numpy as np

        self.assertEqual(decode_value(np.asarray(img.convert("RGB"))), frame_value(3.5))
        # EXIF
        ex = piexif.load(img.info["exif"])
        lat = dms_to_deg(ex["GPS"][piexif.GPSIFD.GPSLatitude])
        self.assertAlmostEqual(lat, LAT0 + 11.5 * DLAT, places=6)
        self.assertEqual(ex["Exif"][piexif.ExifIFD.DateTimeOriginal], b"2026:11:18 15:00:11")
        img.close()
        with open(os.path.join(res.job_dir, "run_summary.json"), encoding="utf-8") as fh:
            summary = json.load(fh)
        self.assertEqual(summary["counts"]["exported"], 12)
        self.assertEqual(summary["time_mode"], "clock")

    def test_nmea_vfr_rotated(self):
        al = Alignment(0.0, T0 + 30.0)
        res = self._run(self.vfr, self.nmea, al, rate=5, image_format="png")
        rows = read_csv(os.path.join(res.job_dir, "frames.csv"))
        self.assertGreaterEqual(len(rows), 29)
        import numpy as np

        for r in rows[::5]:
            img = np.asarray(Image.open(os.path.join(res.job_dir, "images", r["image_file"])).convert("RGB"))
            self.assertEqual(img.shape[:2], (64, 48))  # rotated portrait
            self.assertEqual(decode_value(img), frame_value(float(r["video_pts"])))
            self.assertLess(abs(float(r["video_pts"]) - round(float(r["video_pts"]) * 5) / 5), 0.04)

    def test_mpegts_seek_lands_on_requested_frame(self):
        # MPEG-TS seeks can land on a keyframe after the target; frame_at must back off.
        ts = os.path.join(self.tmp.name, "v.ts")
        make_video(ts, seconds=4.0, fps=30, codec="mpeg2video")
        src = open_video(ts)
        try:
            import numpy as np

            for q in (1.0, 2.5, 3.3):
                pts, img = src.frame_at(q)
                self.assertLess(abs(pts - q), 0.5 / 30 + 0.04, msg=f"seek {q} -> {pts}")
                # the TS muxer shifts timestamps by start_pts relative to the encoded clock
                self.assertEqual(decode_value(np.asarray(img)), frame_value(pts - src.info.start_pts))
        finally:
            src.close()

    def test_export_by_distance(self):
        al = Alignment(0.0, T0 + 10.0)
        res = self._run(self.video, self.gpx, al, distance_m=10.0)
        rows = read_csv(os.path.join(res.job_dir, "frames.csv"))
        self.assertEqual(res.exported, 3)  # 6 s at 5 m/s = 30 m -> 0, 10, 20 m
        pts = [float(r["video_pts"]) for r in rows]
        for a, b in zip(pts, pts[1:]):
            self.assertAlmostEqual(b - a, 2.0, delta=0.05)
        with open(os.path.join(res.job_dir, "run_summary.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["config"]["distance_m"], 10.0)

    def test_distance_vfr_no_duplicate_frames(self):
        # every candidate is kept, but VFR holes make neighbours share a frame
        al = Alignment(0.0, T0 + 30.0)
        res = self._run(self.vfr, self.gpx, al, distance_m=0.05)
        rows = read_csv(os.path.join(res.job_dir, "frames.csv"))
        pts = [r["video_pts"] for r in rows]
        self.assertEqual(len(pts), len(set(pts)))
        skipped = read_csv(os.path.join(res.job_dir, "skipped.csv"))
        self.assertTrue(any(r["reason"] == "duplicate_frame" for r in skipped))

    def test_no_track_relative(self):
        res = self._run(self.video, None, None, rate=1, relative_date="20261118")
        rows = read_csv(os.path.join(res.job_dir, "frames.csv"))
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[1]["image_file"], "20261118_000001000.jpg")
        self.assertEqual(rows[1]["latitude"], "")

    def test_cancel(self):
        out = os.path.join(self.tmp.name, "out_cancel")
        ev = threading.Event()
        ev.set()
        src = open_video(self.video)
        try:
            res = run_export(src, load_track(self.gpx), Alignment(0, T0), ExportConfig(rate=10), out,
                             cancel=ev)
        finally:
            src.close()
        self.assertTrue(res.cancelled)
        self.assertTrue(os.path.exists(os.path.join(res.job_dir, "run_summary.json")))

    def test_job_dirs_not_overwritten(self):
        al = Alignment(0.0, T0)
        a = self._run(self.video, self.gpx, al, rate=1)
        b = self._run(self.video, self.gpx, al, rate=1)
        self.assertNotEqual(a.job_dir, b.job_dir)


if __name__ == "__main__":
    unittest.main()
