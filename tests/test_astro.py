import unittest
from datetime import datetime, timedelta, timezone

from telescopius_bridge import astro

# Reference values computed with astropy 7 (geometric AltAz, no refraction;
# FK5 mean equinox of date), Paris 48.8566 N 2.3522 E.
LAT, LON = 48.8566, 2.3522
CASES = [
    # utc, ra, dec, alt, az, ra_of_date, dec_of_date
    ("2026-11-04T22:00:00", 10.684708, 41.26875, 81.6331, 209.3184, 11.05344, 41.4155),
    ("2026-11-05T02:30:00", 83.6433, -3.3076, 37.8556, 180.2678, 83.97868, -3.2915),
    ("2031-06-01T01:00:00", 279.2347, 38.7837, 76.3802, 132.7295, 279.49848, 38.81217),
]


def utc(iso):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class AstroTest(unittest.TestCase):
    def test_alt_az_matches_astropy(self):
        for iso, ra, dec, alt_ref, az_ref, *_ in CASES:
            alt, az = astro.alt_az(ra, dec, LAT, LON, utc(iso))
            self.assertAlmostEqual(alt, alt_ref, delta=0.02, msg=iso)
            self.assertAlmostEqual(az, az_ref, delta=0.05, msg=iso)

    def test_precession_matches_astropy(self):
        for iso, ra, dec, _, _, ra_ref, dec_ref in CASES:
            ra2, dec2 = astro.precess_from_j2000(ra, dec, astro.julian_date(utc(iso)))
            self.assertAlmostEqual(ra2, ra_ref, delta=0.0005)
            self.assertAlmostEqual(dec2, dec_ref, delta=0.0005)

    def test_transit_is_due_south(self):
        when = utc("2026-11-04T19:00:00")
        transit = astro.nearest_transit(83.6433, -3.3076, LON, when)
        self.assertLessEqual(abs((transit - when).total_seconds()), 12 * 3600)
        _, az = astro.alt_az(83.6433, -3.3076, LAT, LON, transit)
        self.assertAlmostEqual(az, 180.0, delta=0.1)

    def test_unit_vector_round_trip(self):
        x, y, z = astro.unit_vector(38.21, 61.471111)
        self.assertAlmostEqual(x * x + y * y + z * z, 1.0)
        import math
        self.assertAlmostEqual(math.degrees(math.atan2(y, x)), 38.21)
        self.assertAlmostEqual(math.degrees(math.asin(z)), 61.471111)

    def test_sun_altitude_matches_astropy(self):
        for iso, ref in (("2026-11-05T16:24:13", -0.864), ("2026-11-05T18:11:52", -18.012),
                         ("2026-06-21T12:00:00", 64.536), ("2027-03-20T06:00:00", 0.115)):
            self.assertAlmostEqual(astro.sun_altitude(LAT, LON, utc(iso)), ref, delta=0.05, msg=iso)

    def test_night_paris(self):
        expected_sunset = utc("2026-11-05T16:24:00")
        for ref in ("2026-11-05T09:00:00", "2026-11-05T15:00:00", "2026-11-05T22:00:00"):
            n = astro.night(LAT, LON, utc(ref))
            sunset = datetime.fromisoformat(n["events"]["sunset"])
            self.assertLess(abs((sunset - expected_sunset).total_seconds()), 120, ref)
            self.assertLess(abs((datetime.fromisoformat(n["events"]["astronomical_dusk"])
                                 - utc("2026-11-05T18:12:00")).total_seconds()), 120)
            self.assertEqual(len(n["sun_alt"]), n["count"])
            start = datetime.fromisoformat(n["start"])
            self.assertLess(start, sunset)
            end = start + (n["count"] - 1) * timedelta(minutes=n["step_minutes"])
            self.assertGreater(end, datetime.fromisoformat(n["events"]["sunrise"]))
        # After midnight, still the current night.
        n = astro.night(LAT, LON, utc("2026-11-06T02:00:00"))
        self.assertEqual(n["events"]["sunset"][:10], "2026-11-05")

    def test_night_without_sunset(self):
        n = astro.night(69.65, 18.96, utc("2026-06-21T12:00:00"))  # Tromsø, midnight sun
        self.assertIsNone(n["events"]["sunset"])
        self.assertTrue(all(a > -1 for a in n["sun_alt"]))
        self.assertGreater(n["count"], 90)

    def test_altitude_series_matches_alt_az(self):
        start = utc("2026-11-05T17:00:00")
        series = astro.altitude_series(10.684708, 41.26875, LAT, LON, start, 30, 5)
        for i, alt in enumerate(series):
            ref, _ = astro.alt_az(10.684708, 41.26875, LAT, LON, start + timedelta(minutes=30 * i))
            self.assertAlmostEqual(alt, ref, delta=0.06)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            astro.julian_date(datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
