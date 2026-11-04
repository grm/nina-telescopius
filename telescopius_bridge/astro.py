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
