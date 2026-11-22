"""Regression tests for the pre-release review findings."""
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from fixtures import T0, expected_lat, make_video, write_gpx

from vtl import align
from vtl.cli import main as cli_main
from vtl.export import run_export
from vtl.models import Alignment, ExportConfig
from vtl.sampler import build_schedule
from vtl.timefmt import fmt_offset, parse_offset
from vtl.track import TRACK_EXTENSIONS, load_track
from vtl.video import open_video


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def cli(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli_main(list(argv))
        return code, err.getvalue()


class OffsetFormatTests(unittest.TestCase):
    def test_millisecond_offsets_round_trip(self):
        self.assertEqual(fmt_offset(0.225), "+0.225")
        self.assertEqual(fmt_offset(-12.345), "-12.345")
        self.assertEqual(fmt_offset(28800.125), "+8:00:00.125")
        self.assertEqual(fmt_offset(3599.9996), "+1:00:00")
        for v in (0.125, -0.001, 601.007, -28800.5):
            self.assertAlmostEqual(parse_offset(fmt_offset(v)), v, places=6)


class StoreTests(TmpDirTest):
    def test_recall_ignores_damaged_entry(self):
        video = self.path("v.mp4")
        with open(video, "wb") as fh:
            fh.write(b"x")
        store = self.path("alignments.json")
        with mock.patch.object(align, "_store_path", return_value=store):
            with open(store, "w", encoding="utf-8") as fh:
                json.dump({align._video_key(video): ["not", "a", "dict"]}, fh)
            self.assertIsNone(align.recall(video))
            align.remember(video, {"offset": "1"})
            self.assertEqual(align.recall(video), {"offset": "1"})


class TrackExtensionTests(unittest.TestCase):
    def test_ui_accepts_every_loader_extension(self):
        try:
            from vtl_ui.app import TRACK_EXTS
        except Exception as exc:  # noqa: BLE001 - no Tk on this machine
            self.skipTest(f"UI not importable: {exc}")
        for ext in TRACK_EXTENSIONS:
            self.assertIn(ext, TRACK_EXTS)
        self.assertIn(".ubx_nmea", TRACK_EXTS)


class CliTests(TmpDirTest):
    def setUp(self):
        super().setUp()
        self.video = self.path("v.mp4")
        make_video(self.video, seconds=2.0)

    def test_date_with_slashes_in_relative_mode(self):
        out = self.path("out")
        code, err = self.cli("export", "--video", self.video, "--out", out,
                             "--date", "2026/11/18", "--rate", "1")
        self.assertEqual(code, 0, err)
        job = os.path.join(out, os.listdir(out)[0])
        images = sorted(os.listdir(os.path.join(job, "images")))
        self.assertTrue(images)
        self.assertTrue(all(n.startswith("20261118_") for n in images), images)

    def test_anchor2_without_anchor1_is_an_error(self):
        code, err = self.cli("export", "--video", self.video, "--out", self.path("out"),
                             "--anchor2-pts", "1.5", "--anchor2-time", "2026-11-18 15:00:00")
        self.assertEqual(code, 2)
        self.assertIn("--anchor2-time needs --anchor-time", err)

    def test_non_finite_rate_is_an_error(self):
        code, err = self.cli("export", "--video", self.video, "--out", self.path("out"),
                             "--rate", "inf")
        self.assertEqual(code, 2)
        self.assertIn("finite", err)

    def test_unwritable_output_is_an_error_not_a_traceback(self):
        blocker = self.path("blocker")
        with open(blocker, "w") as fh:
            fh.write("a file where the output folder should be")
        code, err = self.cli("export", "--video", self.video, "--out", os.path.join(blocker, "sub"))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("Error:"), err)


class NonFiniteScheduleTests(unittest.TestCase):
    def test_build_schedule_rejects_nan_and_inf(self):
        from vtl.models import VideoInfo

        info = VideoInfo("v", 10.0, 0.0, 10.0, 30.0, False, 64, 48, 0, "h264", None, "test")
        for cfg in (ExportConfig(rate=float("inf")), ExportConfig(rate=float("nan")),
                    ExportConfig(distance_m=float("inf"))):
            with self.assertRaises(ValueError):
                build_schedule(info, None, None, cfg)


class ActualFrameGeotagTests(TmpDirTest):
    def test_time_and_position_follow_the_written_frame(self):
        """5 fps video, grid 0.13 s off the frames: tags must match the frame, not the slot."""
        video = self.path("slow.mp4")
        make_video(video, seconds=6.0, fps=5)
        gpx = self.path("t.gpx")
        write_gpx(gpx, seconds=30)
        track = load_track(gpx)
        alignment = Alignment(0.0, T0, 1.0)
        cfg = ExportConfig(rate=1.0, grid_start=0.13, in_pts=0.13, write_exif=False)
        src = open_video(video)
        try:
            res = run_export(src, track, alignment, cfg, self.path("out"))
        finally:
            src.close()
        with open(os.path.join(res.job_dir, "frames.csv"), encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        self.assertGreaterEqual(len(rows), 4)
        for r in rows:
            pts = float(r["video_pts"])
            self.assertAlmostEqual(pts * 5, round(pts * 5), places=3)  # a real frame time
            self.assertAlmostEqual(float(r["latitude"]), expected_lat(T0 + pts), places=7)
            # filename carries the same instant (UTC here): seconds + milliseconds of the frame
            stamp = r["image_file"].split("_")[1].split(".")[0]
            ms_of_minute = int(stamp[4:6]) * 1000 + int(stamp[6:9])
            self.assertEqual(ms_of_minute, int(round(pts * 1000)))


if __name__ == "__main__":
    unittest.main()
