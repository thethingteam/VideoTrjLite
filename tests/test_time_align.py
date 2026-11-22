import os
import unittest
from datetime import date
from unittest import mock

from fixtures import T0

from vtl.align import _store_path, from_metadata, from_single_anchor, from_two_anchors, scale_warning
from vtl.models import Alignment, Anchor
from vtl.timefmt import (
    filename_stamp,
    fmt_local,
    format_tz,
    parse_tz,
    parse_user_time,
    relative_stamp,
)

D = date(2026, 11, 18)
TZ8 = 480


class StorePathTests(unittest.TestCase):
    def test_appdata_wins(self):
        with mock.patch.dict(os.environ, {"APPDATA": os.path.join("X", "roaming")}):
            self.assertEqual(
                _store_path(),
                os.path.join("X", "roaming", "video_trj_integration_lite", "alignments.json"),
            )

    def test_xdg_config_home_without_appdata(self):
        env = {k: v for k, v in os.environ.items() if k != "APPDATA"}
        env["XDG_CONFIG_HOME"] = os.path.join("X", "cfg")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.platform", "linux"):
            self.assertEqual(
                _store_path(),
                os.path.join("X", "cfg", "video_trj_integration_lite", "alignments.json"),
            )


class TimeFormatTests(unittest.TestCase):
    def test_tz(self):
        self.assertEqual(parse_tz("UTC+08:00"), 480)
        self.assertEqual(parse_tz("+8"), 480)
        self.assertEqual(parse_tz("-0530"), -330)
        self.assertEqual(parse_tz("UTC"), 0)
        self.assertEqual(format_tz(-330), "UTC-05:30")
        with self.assertRaises(ValueError):
            parse_tz("Asia/Taipei")

    def test_user_time_variants(self):
        # 15:00:00 local (+8) == 07:00:00 UTC == T0
        for text in (
            "15:00:00",
            "15:00",
            "下午 03:00:00",
            "03:00:00 PM",
            "2026-11-18 15:00:00",
            "2026/11/18 15:00:00.000",
            "2026-11-18T15:00:00",
            "2026-11-18T07:00:00Z",
            "2026-11-18T15:00:00+08:00",
        ):
            with self.subTest(text=text):
                self.assertAlmostEqual(parse_user_time(text, TZ8, D), T0, places=6)
        self.assertAlmostEqual(parse_user_time("15:00:01.25", TZ8, D), T0 + 1.25, places=6)
        self.assertAlmostEqual(parse_user_time("上午 12:00:00", TZ8, D), T0 - 15 * 3600, places=6)

    def test_user_time_errors(self):
        for bad in ("", "25:00:00", "下午 13:00:00", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_user_time(bad, TZ8, D)
        with self.assertRaises(ValueError):
            parse_user_time("15:00:00", TZ8, None)

    def test_offset(self):
        from vtl.timefmt import fmt_offset, parse_offset

        self.assertEqual(parse_offset(""), 0.0)
        self.assertEqual(parse_offset("-12.5"), -12.5)
        self.assertEqual(parse_offset("+8:00:00"), 28800.0)
        self.assertEqual(parse_offset("-1:02.5"), -62.5)
        with self.assertRaises(ValueError):
            parse_offset("abc")
        self.assertEqual(fmt_offset(1.2), "+1.2")
        self.assertEqual(fmt_offset(-0.1), "-0.1")
        self.assertEqual(fmt_offset(0), "+0")
        self.assertEqual(fmt_offset(28800.5), "+8:00:00.5")
        for v in (1.2, -0.1, 28800.5, -3600.0):
            self.assertAlmostEqual(parse_offset(fmt_offset(v)), v)

    def test_formatting(self):
        self.assertEqual(filename_stamp(T0 + 0.25, TZ8), "20261118_150000250")
        self.assertEqual(filename_stamp(T0 + 0.9996, TZ8), "20261118_150001000")
        self.assertEqual(fmt_local(T0 + 1.5, TZ8), "2026-11-18 15:00:01.500")
        self.assertEqual(relative_stamp(3661.5, "20261118"), "20261118_010101500")


class AlignTests(unittest.TestCase):
    def test_single_anchor_roundtrip(self):
        al = from_single_anchor(Anchor(12.5, T0))
        self.assertAlmostEqual(al.to_utc(12.5), T0)
        self.assertAlmostEqual(al.to_utc(14.0), T0 + 1.5)
        self.assertAlmostEqual(al.to_pts(al.to_utc(33.3)), 33.3)
        self.assertAlmostEqual(al.shifted(0.2).to_utc(12.5), T0 + 0.2)

    def test_two_anchors_scale(self):
        al = from_two_anchors(Anchor(100.0, T0 + 100.05), Anchor(0.0, T0))
        self.assertAlmostEqual(al.scale, 1.0005)
        self.assertAlmostEqual(al.to_utc(100.0), T0 + 100.05)
        self.assertIsNone(scale_warning(al))
        self.assertIsNotNone(scale_warning(Alignment(0, T0, 1.01)))
        with self.assertRaises(ValueError):
            from_two_anchors(Anchor(0, T0), Anchor(0.5, T0 + 0.5))
        with self.assertRaises(ValueError):
            from_two_anchors(Anchor(0, T0), Anchor(10, T0 - 10))

    def test_metadata(self):
        al = from_metadata(T0, 0.1, offset_s=-2.0)
        self.assertAlmostEqual(al.to_utc(0.1), T0 - 2.0)


if __name__ == "__main__":
    unittest.main()
