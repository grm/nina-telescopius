"""Small, dependency-free astronomy helpers.

Accuracy targets are "planning grade" (well under 0.1 deg): good enough to show
altitudes and transit times, and to build the view vectors Stellarium reports.
Coordinates handed to NINA are never transformed here: they stay J2000 as
returned by Telescopius.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

J2000_JD = 2451545.0
SIDEREAL_RATE = 360.98564736629  # degrees of sidereal rotation per solar day


def julian_date(when: datetime) -> float:
    if when.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    unix = when.astimezone(timezone.utc).timestamp()
    return unix / 86400.0 + 2440587.5


def gmst_deg(jd: float) -> float:
    """Greenwich mean sidereal time (IAU 1982), in degrees."""
    t = (jd - J2000_JD) / 36525.0
    gmst = (
        280.46061837
        + SIDEREAL_RATE * (jd - J2000_JD)
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    return gmst % 360.0


def lst_deg(jd: float, longitude_deg: float) -> float:
    return (gmst_deg(jd) + longitude_deg) % 360.0


def precess_from_j2000(ra_deg: float, dec_deg: float, jd: float) -> Tuple[float, float]:
    """Rigorous IAU 1976 precession from J2000 to the mean equinox of date."""
    t = (jd - J2000_JD) / 36525.0
    arcsec = math.pi / (180.0 * 3600.0)
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * arcsec
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * arcsec
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * arcsec

    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    a = math.cos(dec) * math.sin(ra + zeta)
    b = math.cos(theta) * math.cos(dec) * math.cos(ra + zeta) - math.sin(theta) * math.sin(dec)
    c = math.sin(theta) * math.cos(dec) * math.cos(ra + zeta) + math.cos(theta) * math.sin(dec)

    ra_out = (math.degrees(math.atan2(a, b)) + math.degrees(z)) % 360.0
    dec_out = math.degrees(math.asin(max(-1.0, min(1.0, c))))
    return ra_out, dec_out


def unit_vector(ra_deg: float, dec_deg: float) -> List[float]:
    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    return [math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)]


def alt_az(
    ra_deg: float, dec_deg: float, latitude_deg: float, longitude_deg: float, when: datetime
) -> Tuple[float, float]:
    """Geometric altitude and azimuth (north = 0, east = 90) of a J2000 position."""
    jd = julian_date(when)
    ra, dec = precess_from_j2000(ra_deg, dec_deg, jd)
    hour_angle = math.radians((lst_deg(jd, longitude_deg) - ra) % 360.0)
    lat = math.radians(latitude_deg)
    d = math.radians(dec)

    sin_alt = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(hour_angle)
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    az = math.atan2(
        -math.cos(d) * math.sin(hour_angle),
        math.sin(d) * math.cos(lat) - math.cos(d) * math.sin(lat) * math.cos(hour_angle),
    )
    return math.degrees(alt), math.degrees(az) % 360.0


def stellarium_altaz_vector(alt_deg: float, az_deg: float) -> List[float]:
    """Stellarium's horizontal frame: x towards south, y towards east, z zenith."""
    alt = math.radians(alt_deg)
    az = math.radians(az_deg)
    return [-math.cos(alt) * math.cos(az), math.cos(alt) * math.sin(az), math.sin(alt)]


def culmination_altitude(dec_deg: float, latitude_deg: float) -> float:
    return 90.0 - abs(latitude_deg - dec_deg)


def nearest_transit(
    ra_deg: float, dec_deg: float, longitude_deg: float, when: datetime
) -> datetime:
    """Upper transit closest to `when` (within +/- 12 sidereal hours)."""
    jd = julian_date(when)
    ra, _ = precess_from_j2000(ra_deg, dec_deg, jd)
    delta_deg = (ra - lst_deg(jd, longitude_deg) + 180.0) % 360.0 - 180.0
    return when + timedelta(days=delta_deg / SIDEREAL_RATE)


def visibility_summary(
    ra_deg: float,
    dec_deg: float,
    latitude_deg: float,
    longitude_deg: float,
    when: Optional[datetime] = None,
) -> Dict[str, object]:
    """Altitude now, culmination altitude and nearest transit, for list targets
    (Telescopius does not return visibility data for saved lists)."""
    when = when or datetime.now(timezone.utc)
    alt, az = alt_az(ra_deg, dec_deg, latitude_deg, longitude_deg, when)
    transit = nearest_transit(ra_deg, dec_deg, longitude_deg, when)
    return {
        "alt_now": round(alt, 1),
        "az_now": round(az, 1),
        "max_alt": round(culmination_altitude(dec_deg, latitude_deg), 1),
        "transit_utc": transit.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
    }


# -- Sun and night ------------------------------------------------------------
SUNSET_ALT = -0.833  # upper limb on the horizon, with standard refraction
TWILIGHT_LIMITS = (("civil", -6.0), ("nautical", -12.0), ("astronomical", -18.0))


def sun_radec_of_date(jd: float) -> Tuple[float, float]:
    """Low-precision solar position (Astronomical Almanac, ~0.01 deg), equinox of date."""
    n = jd - J2000_JD
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360.0)
    ecl_lon = math.radians(mean_lon + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    ra = math.degrees(math.atan2(math.cos(obliquity) * math.sin(ecl_lon), math.cos(ecl_lon))) % 360.0
    dec = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecl_lon)))
    return ra, dec


def _altitude_of_date(ra_deg: float, dec_deg: float, latitude_deg: float, longitude_deg: float,
                      jd: float) -> float:
    hour_angle = math.radians((lst_deg(jd, longitude_deg) - ra_deg) % 360.0)
    lat = math.radians(latitude_deg)
    d = math.radians(dec_deg)
    sin_alt = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(hour_angle)
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


def sun_altitude(latitude_deg: float, longitude_deg: float, when: datetime) -> float:
    jd = julian_date(when)
    ra, dec = sun_radec_of_date(jd)
    return _altitude_of_date(ra, dec, latitude_deg, longitude_deg, jd)


def altitude_series(ra_deg: float, dec_deg: float, latitude_deg: float, longitude_deg: float,
                    start: datetime, step_minutes: int, count: int) -> List[float]:
    """Altitudes of a J2000 position at `count` instants `step_minutes` apart.
    Precession is computed once: it drifts by well under an arcsecond per night."""
    jd0 = julian_date(start)
    ra, dec = precess_from_j2000(ra_deg, dec_deg, jd0)
    step_days = step_minutes / 1440.0
    return [round(_altitude_of_date(ra, dec, latitude_deg, longitude_deg, jd0 + i * step_days), 1)
            for i in range(count)]


def _crossing(times: List[datetime], values: List[float], level: float, falling: bool) -> Optional[datetime]:
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        if (falling and a > level >= b) or (not falling and a < level <= b):
            frac = (a - level) / (a - b)
            return times[i - 1] + (times[i] - times[i - 1]) * frac
    return None


def _iso(when: Optional[datetime]) -> Optional[str]:
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat() if when else None


def night(latitude_deg: float, longitude_deg: float, when: Optional[datetime] = None,
          step_minutes: int = 10, margin_minutes: int = 60) -> Dict[str, object]:
    """Sampling grid covering the coming (or current) night, from one hour before
    sunset to one hour after sunrise, with the Sun's altitude and twilight times.

    The night is the one following the local solar noon before `when`, unless its
    sunrise is already past (morning), in which case the next one is used. Where the
    Sun does not set (or rise), local solar 16:00 to 08:00 is used.
    """
    when = when or datetime.now(timezone.utc)
    solar_noon_offset = timedelta(hours=12 - longitude_deg / 15.0)
    day0 = when.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    noon = day0 + solar_noon_offset
    while noon > when:
        noon -= timedelta(days=1)
    while noon + timedelta(days=1) <= when:
        noon += timedelta(days=1)

    for _ in range(2):
        coarse = [noon + timedelta(minutes=m) for m in range(0, 24 * 60 + 1, step_minutes)]
        sun = [sun_altitude(latitude_deg, longitude_deg, t) for t in coarse]
        sunset = _crossing(coarse, sun, SUNSET_ALT, falling=True)
        sunrise = _crossing(coarse, sun, SUNSET_ALT, falling=False)
        if sunrise and sunset and sunrise < sunset:
            sunrise = None  # rising before setting: not this night's sunrise
        if sunrise and when > sunrise + timedelta(minutes=margin_minutes):
            noon += timedelta(days=1)
            continue
        break

    if sunset and sunrise:
        start = sunset - timedelta(minutes=margin_minutes)
        end = sunrise + timedelta(minutes=margin_minutes)
    else:
        start = noon + timedelta(hours=4)
        end = noon + timedelta(hours=20)
    step = timedelta(minutes=step_minutes)
    start = datetime.fromtimestamp((start.timestamp() // step.total_seconds()) * step.total_seconds(), timezone.utc)
    count = int((end - start) / step) + 2

    times = [start + i * step for i in range(count)]
    sun = [round(sun_altitude(latitude_deg, longitude_deg, t), 1) for t in times]
    events: Dict[str, Optional[str]] = {
        "sunset": _iso(_crossing(times, sun, SUNSET_ALT, falling=True)),
        "sunrise": _iso(_crossing(times, sun, SUNSET_ALT, falling=False)),
    }
    for name, level in TWILIGHT_LIMITS:
        events[f"{name}_dusk"] = _iso(_crossing(times, sun, level, falling=True))
        events[f"{name}_dawn"] = _iso(_crossing(times, sun, level, falling=False))
    return {"start": _iso(start), "step_minutes": step_minutes, "count": count,
            "sun_alt": sun, "events": events}
