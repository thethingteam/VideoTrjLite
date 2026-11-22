"""Drives the Tk UI programmatically (skipped when no display is available)."""
import os
import tempfile
import time
import unittest

from fixtures import make_video, write_gpx, write_nmea

from vtl.timefmt import fmt_pts

try:
    import tkinter as tk

    _root = tk.Tk()
    _root.destroy()
    HAS_DISPLAY = True
except Exception:  # noqa: BLE001
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "no display")
class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.video = os.path.join(d, "clip.mp4")
        make_video(cls.video, seconds=6.0, fps=30, size=(160, 120))
        cls.meta_video = os.path.join(d, "meta.mp4")
        # recorded start = track second 20 (07:00:20Z)
        make_video(cls.meta_video, seconds=6.0, fps=30, size=(160, 120),
                   creation_time="2026-11-18T07:00:20.000000Z")
        cls.gpx = os.path.join(d, "t.gpx")
        write_gpx(cls.gpx, seconds=60)
        cls.gga = os.path.join(d, "gga.nmea")
        write_nmea(cls.gga, seconds=60, with_rmc=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from vtl_ui import app as appmod

        self.appmod = appmod
        self._orig = (appmod.messagebox.askyesno, appmod.messagebox.showerror,
                      appmod.simpledialog.askstring, appmod.remember, appmod.recall)
        self.errors = []
        appmod.messagebox.askyesno = lambda *a, **k: False
        appmod.messagebox.showerror = lambda *a, **k: self.errors.append(a)
        appmod.simpledialog.askstring = lambda *a, **k: "2026-11-18"
        appmod.remember = lambda *a, **k: None
        appmod.recall = lambda *a, **k: None
        self.app = appmod.App()
        self.app.v_tz.set("UTC+08:00")

    def tearDown(self):
        a = self.appmod
        (a.messagebox.askyesno, a.messagebox.showerror, a.simpledialog.askstring,
         a.remember, a.recall) = self._orig
        self.app.preview.close()
        self.app.destroy()

    def pump(self, n=10):
        for _ in range(n):
            self.app.update()
            time.sleep(0.02)

    def test_offset_against_track_start(self):
        a = self.app
        a._load_video(self.video)
        a._load_track(self.gpx)
        a.v_offset.set("8")
        a._nudge(1)
        a._nudge(-1)
        a._seek(3.5)
        self.pump()
        text = a.lbl_cur.cget("text")
        self.assertIn("2026-11-18 15:00:11.500", text)
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:00.000")
        self.assertIn("GPS track start", a.lbl_start_src.cget("text"))

    def test_typed_start_time(self):
        a = self.app
        a._load_video(self.video)
        a.v_start.set("15:00:05")
        a._load_track(self.gpx)  # must not overwrite what the user typed
        self.assertEqual(a.v_start.get(), "15:00:05")
        a.v_offset.set("+0.5")
        a._seek(1.0)
        self.pump()
        self.assertIn("2026-11-18 15:00:06.500", a.lbl_cur.cget("text"))
        self.assertIn("typed", a.lbl_start_src.cget("text"))
        a.v_start.set("2026-11-18 15:59:00")
        self.pump()
        self.assertIn("outside the GPS track", a.lbl_cur.cget("text"))
        a.v_start.set("not a time")
        self.pump()
        self.assertIn("Time at sync frame:", a.lbl_cur.cget("text"))
        a._autofill_start(force=True)
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:00.000")

    def test_metadata_base_and_export(self):
        a = self.app
        a._load_video(self.meta_video)
        a._load_track(self.gpx)
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:20.000")
        self.assertIn("video metadata", a.lbl_start_src.cget("text"))
        a.v_offset.set("-0.5")
        a.v_fps.set("1")
        a.v_outdir.set(os.path.join(self.tmp.name, "out"))
        a._seek(0.0)
        self.pump()
        self.assertIn("15:00:19.500", a.lbl_cur.cget("text"))
        a._start_export()
        t0 = time.time()
        while a._export_thread.is_alive() and time.time() - t0 < 30:
            self.pump(1)
        self.pump()
        self.assertEqual(self.errors, [])
        self.assertIn("Done: 6 images", a.lbl_status.cget("text"))

    def test_distance_mode(self):
        a = self.app
        a._load_video(self.video)
        a.v_mode.set(self.appmod.MODE_DIST)
        a._mode_changed()
        a.v_dist.set("10")
        with self.assertRaises(ValueError):  # no track yet
            a._build_config()
        a._load_track(self.gpx)
        cfg = a._build_config()
        self.assertEqual(cfg.distance_m, 10.0)
        self.assertEqual(str(a.cb_fps.cget("state")), "disabled")
        a.v_dist.set("0")
        with self.assertRaises(ValueError):
            a._build_config()
        self.assertEqual(a._saved_state()["mode"], self.appmod.MODE_DIST)

    def test_nmea_without_date_prompts(self):
        a = self.app
        a._load_video(self.video)
        a._load_track(self.gga)
        self.assertIsNotNone(a.track)
        self.assertEqual(self.errors, [])

    def test_outside_track_hint(self):
        a = self.app
        a._load_video(self.video)
        a._load_track(self.gpx)
        a.v_offset.set("-1:00:00")
        self.pump()
        self.assertIn("outside the GPS track", a.lbl_cur.cget("text"))

    def test_sync_frame_and_export_from(self):
        import csv

        a = self.app
        a._load_video(self.video)
        a._load_track(self.gpx)
        a.v_adv.set(True)
        a._toggle_advanced()
        # sync: the frame at 2.0 s shows 15:00:30
        a._seek(2.0)
        a._use_current_as_sync()
        self.assertEqual(a.v_sync.get(), "0:00:02.000")
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:02.000")  # auto: track start + 2 s
        a.v_start.set("15:00:30")
        a._seek(3.0)
        self.pump()
        self.assertIn("15:00:31.000", a.lbl_cur.cget("text"))
        self.assertIn("frame ≈ 60", a.lbl_sync_frame.cget("text"))
        # moving the sync frame keeps the typed alignment
        a._seek(4.0)
        a._use_current_as_sync()
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:32.000")
        # export starts at the chosen frame
        a._seek(1.5)
        a.v_in.set(fmt_pts(a.cur_pts))
        a.v_fps.set("1")
        a.v_outdir.set(os.path.join(self.tmp.name, "out_sync"))
        a._start_export()
        t0 = time.time()
        while a._export_thread.is_alive() and time.time() - t0 < 30:
            self.pump(1)
        self.pump()
        self.assertEqual(self.errors, [])
        self.assertIn("Done: 5 images", a.lbl_status.cget("text"))  # 1.5, 2.5, .. 5.5
        job = a.lbl_status.cget("text").split("→")[-1].strip()
        with open(os.path.join(job, "frames.csv"), encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertAlmostEqual(float(rows[0]["video_pts"]), 1.5, delta=0.02)
        self.assertTrue(rows[0]["local_time"].startswith("2026-11-18T15:00:29.5")
                        or rows[0]["local_time"].startswith("2026-11-18 15:00:29.5"), rows[0]["local_time"])

    def test_old_saved_clock_settings(self):
        a = self.app
        a._load_video(self.video)
        a._apply_saved({"base": "Clock time at a frame", "anchor_pts": 2.0,
                        "anchor_time": "15:00:30", "offset": "0"})
        self.assertEqual(a.v_sync.get(), "0:00:02.000")
        self.assertEqual(a.v_start.get(), "15:00:30")

    def test_old_saved_track_start(self):
        a = self.app
        a._load_video(self.meta_video)  # has metadata, but v0.1 was set to "Track start"
        a._apply_saved({"base": "Track start", "offset": "1"})
        a._load_track(self.gpx)
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:00.000")
        self.assertIn("GPS track start", a.lbl_start_src.cget("text"))
        a._load_video(self.meta_video)  # a fresh load goes back to the default
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:20.000")

    def test_export_to_is_per_video(self):
        a = self.app
        a._load_video(self.video)
        a.v_out_pts.set("3")
        self.assertEqual(a._saved_state()["to"], "3")
        self.assertEqual(a._build_config().out_pts, 3.0)
        a._load_video(self.meta_video)
        self.assertEqual(a.v_out_pts.get(), "")
        a._apply_saved({"to": "4.5"})
        self.assertEqual(a.v_out_pts.get(), "4.5")
        self.assertTrue(a.v_adv.get())  # shown so the clip is not hidden

    def test_offset_is_per_video(self):
        a = self.app
        a._load_video(self.video)
        a.v_offset.set("+12.5")
        a._load_video(self.meta_video)
        self.assertEqual(a.v_offset.get(), "0")
        a._apply_saved({"offset": "-3"})
        self.assertEqual(a.v_offset.get(), "-3")

    def test_each_dateless_nmea_asks_for_its_date(self):
        a = self.app
        asked = []
        answers = iter(["2026-11-11", "2026-11-18"])

        def ask(*args, **kw):
            asked.append(kw.get("initialvalue"))
            return next(answers)

        self.appmod.simpledialog.askstring = ask
        a._load_track(self.gga)
        first_day = a.track.start
        a._load_track(self.gga)  # a second dateless file must prompt again
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[1], "2026-11-11")  # previous answer is only a suggestion
        self.assertAlmostEqual(a.track.start - first_day, 7 * 86400)

    def test_stale_preview_error_is_ignored(self):
        a = self.app
        a._load_video(self.video)
        self.pump()
        self.assertIsNotNone(a._last_img)
        a.q.put(("preview_error", a.preview.generation - 1, 0.0, "old video failed"))
        self.pump(3)
        self.assertIsNotNone(a._last_img)

    def test_typed_time_survives_bad_timezone(self):
        a = self.app
        a._load_video(self.video)
        a._load_track(self.gpx)
        a.v_start.set("15:00:30")
        a.v_tz.set("UTC+0")  # half-typed, invalid
        a.v_tz.set("UTC+")
        a._seek(2.0)
        a._use_current_as_sync()
        self.assertEqual(a.v_start.get(), "15:00:30")
        self.assertEqual(a.v_sync.get(), "0:00:00.000")
        self.assertIn("Sync frame not changed", a.lbl_status.cget("text"))

    def test_partial_sync_input_keeps_auto_time(self):
        a = self.app
        a._load_video(self.video)
        a._load_track(self.gpx)
        auto = a.v_start.get()
        a.v_sync.set("0:0")  # mid-typing
        a.v_sync.set("0:0x")
        self.assertEqual(a.v_start.get(), auto)
        a.v_sync.set("2")
        self.assertEqual(a.v_start.get(), "2026-11-18 15:00:02.000")
        self.assertIn("GPS track start", a.lbl_start_src.cget("text"))

    def test_parse_position(self):
        p = self.appmod._parse_position
        self.assertIsNone(p("  ", "X"))
        self.assertEqual(p("0:01:02.5", "X"), 62.5)
        with self.assertRaisesRegex(ValueError, "^X:"):
            p("abc", "X")


if __name__ == "__main__":
    unittest.main()
