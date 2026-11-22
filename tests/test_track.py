import os
import tempfile
import unittest
from datetime import date

from fixtures import DLAT, LAT0, T0, nmea_sentence, write_gpx, write_nmea

from vtl.models import GNSS_GAP, OK, OUTSIDE_COVERAGE
from vtl.track import detect_format, load_track
from vtl.track.interp import TrackInterpolator, haversine


class TrackTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return os.path.join(self.dir, name)

    def write(self, name, text, encoding="utf-8"):
        p = self.path(name)
        with open(p, "w", encoding=encoding) as fh:
            fh.write(text)
        return p


class GpxTests(TrackTestBase):
    def test_basic(self):
        p = self.path("a.gpx")
        write_gpx(p, seconds=10)
        tr = load_track(p)
        self.assertEqual(tr.source, "gpx")
        self.assertEqual(len(tr), 11)
        self.assertAlmostEqual(tr.start, T0, places=3)
        self.assertAlmostEqual(tr.points[5].lat, LAT0 + 5 * DLAT, places=7)
        self.assertAlmostEqual(tr.points[0].alt, 10.0)

    def test_gpx10_offsets_missing_time_and_segments(self):
        text = """<?xml version="1.0"?>
<gpx version="1.0" xmlns="http://www.topografix.com/GPX/1/0">
 <trk>
  <trkseg>
   <trkpt lat="25.0" lon="121.0"><time>2026-11-18T15:00:00+08:00</time></trkpt>
   <trkpt lat="25.1" lon="121.0"></trkpt>
   <trkpt lat="25.2" lon="121.0"><time>2026-11-18T07:00:01.5Z</time><speed>3.5</speed></trkpt>
  </trkseg>
  <trkseg>
   <trkpt lat="25.3" lon="121.0"><time>2026-11-18T07:00:02Z</time></trkpt>
  </trkseg>
 </trk>
</gpx>"""
        tr = load_track(self.write("b.gpx", text))
        self.assertEqual(len(tr), 3)
        self.assertAlmostEqual(tr.points[0].t, T0, places=6)
        self.assertAlmostEqual(tr.points[1].t, T0 + 1.5, places=6)
        self.assertEqual(tr.points[1].speed, 3.5)
        self.assertNotEqual(tr.points[1].segment, tr.points[2].segment)
        self.assertTrue(any("missing time" in w for w in tr.warnings))
        it = TrackInterpolator(tr)
        status, _ = it.locate(T0 + 1.75)  # crosses segments
        self.assertEqual(status, GNSS_GAP)

    def test_no_namespace_and_sniffing(self):
        text = '<gpx><trk><trkseg><trkpt lat="1" lon="2"><time>2026-01-01T00:00:00Z</time></trkpt></trkseg></trk></gpx>'
        p = self.write("track.txt", text)
        self.assertEqual(detect_format(p), "gpx")
        self.assertEqual(len(load_track(p)), 1)

    def test_empty_raises(self):
        p = self.write("e.gpx", '<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>')
        with self.assertRaises(ValueError):
            load_track(p)


class GpxMalformedTests(TrackTestBase):
    def test_broken_xml_is_a_value_error(self):
        p = self.path("broken.gpx")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0"?>\n<gpx version="1.1"><trk><trkseg>')
        with self.assertRaises(ValueError) as ctx:
            load_track(p)
        self.assertIn("Not a valid GPX file", str(ctx.exception))


class NmeaTests(TrackTestBase):
    def test_gga_rmc_merge(self):
        p = self.path("a.nmea")
        write_nmea(p, seconds=10)
        tr = load_track(p)
        self.assertEqual(tr.source, "nmea")
        self.assertEqual(len(tr), 11)
        self.assertAlmostEqual(tr.start, T0, places=3)
        p5 = tr.points[5]
        self.assertAlmostEqual(p5.lat, LAT0 + 5 * DLAT, places=6)
        self.assertAlmostEqual(p5.alt, 10.5, places=2)  # from GGA
        self.assertAlmostEqual(p5.speed, 9.72 * 0.514444, places=3)  # from RMC
        self.assertEqual(p5.hdop, 0.8)
        self.assertEqual(p5.sats, 12)

    def test_gga_only_needs_date(self):
        p = self.path("g.log")
        write_nmea(p, seconds=5, with_rmc=False, talker="GP")
        with self.assertRaises(ValueError):
            load_track(p)
        tr = load_track(p, date_hint=date(2026, 11, 18))
        self.assertEqual(len(tr), 6)
        self.assertAlmostEqual(tr.start, T0, places=3)

    def test_bad_checksum_prefix_and_invalid_fix(self):
        good = nmea_sentence("GPRMC,070000.00,A,2500.000000,N,12130.000000,E,0.0,0.0,181126,,,A")
        bad = nmea_sentence("GPRMC,070001.00,A,2500.100000,N,12130.000000,E,0.0,0.0,181126,,,A")
        bad = bad[:-2] + ("00" if bad[-2:] != "00" else "01")
        void = nmea_sentence("GPRMC,070002.00,V,,,,,,,181126,,,N")
        later = nmea_sentence("GPRMC,070003.00,A,2500.300000,N,12130.000000,E,0.0,0.0,181126,,,A")
        text = "\n".join([f"12:00:00.123 {good}", bad, void, later]) + "\n"
        tr = load_track(self.write("x.nmea", text, encoding="ascii"))
        self.assertEqual(len(tr), 2)
        self.assertAlmostEqual(tr.points[0].lat, 25.0)
        self.assertAlmostEqual(tr.points[0].lon, 121.5)
        self.assertTrue(any("checksum" in w for w in tr.warnings))
        self.assertTrue(any("valid fix" in w for w in tr.warnings))

    def test_midnight_rollover_gga_only(self):
        lines = [
            nmea_sentence("GPGGA,235959.00,2500.000000,N,12130.000000,E,1,08,1.0,5.0,M,,M,,"),
            nmea_sentence("GPGGA,000000.00,2500.000100,N,12130.000000,E,1,08,1.0,5.0,M,,M,,"),
            nmea_sentence("GPGGA,000001.00,2500.000200,N,12130.000000,E,1,08,1.0,5.0,M,,M,,"),
        ]
        tr = load_track(self.write("m.nmea", "\n".join(lines)), date_hint=date(2026, 11, 17))
        self.assertEqual(len(tr), 3)
        self.assertAlmostEqual(tr.points[1].t - tr.points[0].t, 1.0, places=3)
        self.assertAlmostEqual(tr.points[1].t, T0 - 7 * 3600, places=3)

    def test_southern_western_hemisphere(self):
        s = nmea_sentence("GPRMC,070000.00,A,3352.500000,S,15112.300000,W,0.0,0.0,181126,,,A")
        tr = load_track(self.write("s.nmea", s))
        self.assertAlmostEqual(tr.points[0].lat, -(33 + 52.5 / 60), places=7)
        self.assertAlmostEqual(tr.points[0].lon, -(151 + 12.3 / 60), places=7)


class InterpTests(TrackTestBase):
    def setUp(self):
        super().setUp()
        p = self.path("t.gpx")
        write_gpx(p, seconds=20, gap=(10, 15))
        self.it = TrackInterpolator(load_track(p), max_gap_s=3.0)

    def test_interpolation(self):
        status, fix = self.it.locate(T0 + 2.25)
        self.assertEqual(status, OK)
        self.assertAlmostEqual(fix.lat, LAT0 + 2.25 * DLAT, places=8)
        self.assertAlmostEqual(fix.alt, 10.225, places=6)
        self.assertAlmostEqual(fix.speed, 5.0, delta=0.05)
        self.assertAlmostEqual(fix.course, 0.0, places=3)
        self.assertAlmostEqual(fix.ref_pre, T0 + 2, places=6)
        self.assertAlmostEqual(fix.ref_next, T0 + 3, places=6)

    def test_exact_point(self):
        status, fix = self.it.locate(T0 + 3)
        self.assertEqual(status, OK)
        self.assertEqual(fix.ref_pre, fix.ref_next)

    def test_gap_and_coverage(self):
        self.assertEqual(self.it.locate(T0 + 12)[0], GNSS_GAP)
        self.assertEqual(self.it.locate(T0 - 0.1)[0], OUTSIDE_COVERAGE)
        self.assertEqual(self.it.locate(T0 + 20.1)[0], OUTSIDE_COVERAGE)
        self.assertEqual(self.it.locate(T0 + 20)[0], OK)
        self.assertEqual(self.it.gap_count(), 1)

    def test_haversine(self):
        self.assertAlmostEqual(haversine(25.0, 121.5, 25.0 + DLAT, 121.5), 5.0, delta=0.05)
        self.assertAlmostEqual(haversine(0, 0, 0, 1), 111_195, delta=5)


if __name__ == "__main__":
    unittest.main()
