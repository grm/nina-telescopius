"""Responses mimicking Stellarium's Remote Control plugin, limited to what
NINA's Stellarium planetarium driver reads (NINA.Equipment/Equipment/
MyPlanetarium/Stellarium.cs):

- GET /api/main/status                    -> location (site import)
- GET /api/objects/info?format=json       -> selected object (404 = none)
- GET /api/stelproperty/list?format=json  -> Oculars.enableCCD and
                                             Oculars.selectedCCDRotationAngle
- GET /api/main/view                      -> view vectors, used by NINA instead
                                             of objects/info when the oculars
                                             CCD is enabled

NINA only reads the rotation angle when Oculars.enableCCD is true, so the CCD
is reported as enabled exactly when the selection carries a rotation.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import astro
from .telescopius import nina_name

FOV_DEG = 1.0


def ocular_angle_from_position_angle(position_angle: float) -> float:
    """NINA turns Stellarium's clockwise ocular angle into a position angle with
    PA = (360 - angle) mod 360; this is the inverse."""
    return (360.0 - position_angle) % 360.0


def _vector_string(v) -> str:
    # Stellarium serialises vectors as a quoted "[x, y, z]" string; NINA strips
    # the quotes before parsing, so keep the exact same shape.
    return "[" + ", ".join(f"{c:.10f}" for c in v) + "]"


def status(config: Dict[str, Any], selection: Optional[Dict[str, Any]], now: Optional[datetime] = None):
    now = now or datetime.now(timezone.utc)
    jd = astro.julian_date(now)
    sel_name = nina_name(selection["target"], config["name_format"]) if selection else ""
    return {
        "location": {
            "altitude": int(round(config["elevation"] or 0)),
            "country": "",
            "landscapeKey": "",
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "name": config.get("location_name") or "Telescopius",
            "planet": "Earth",
            "region": "",
            "role": "",
            "state": "",
        },
        "selectioninfo": html.escape(sel_name),
        "time": {
            "deltaT": 0,
            "gmtShift": 0,
            "isTimeNow": True,
            "jday": jd,
            "timeZone": "UTC",
            "timerate": 1.1574074074074073e-05,
            "utc": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "local": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000"),
        },
        "view": {"fov": FOV_DEG},
    }


def object_info(config: Dict[str, Any], selection: Dict[str, Any], now: Optional[datetime] = None):
    now = now or datetime.now(timezone.utc)
    target = selection["target"]
    ra, dec = float(target["ra"]), float(target["dec"])
    ra_now, dec_now = astro.precess_from_j2000(ra, dec, astro.julian_date(now))
    name = nina_name(target, config["name_format"])
    designations = [d for d in [target.get("id")] + list(target.get("aliases") or []) if d]

    info: Dict[str, Any] = {
        "found": True,
        "name": name,
        "localized-name": name,
        "designations": " - ".join(designations) or name,
        "type": target.get("type_label") or "",
        "object-type": target.get("family") or "",
        "raJ2000": ra,
        "decJ2000": dec,
        "ra": ra_now,
        "dec": dec_now,
        "constellation": target.get("constellation") or "",
    }
    if target.get("mag") is not None:
        info["vmag"] = target["mag"]
        info["vmage"] = target["mag"]
    if target.get("size_arcmin"):
        info["size-deg"] = target["size_arcmin"] / 60.0
    if config["latitude"] is not None and config["longitude"] is not None:
        alt, az = astro.alt_az(ra, dec, config["latitude"], config["longitude"], now)
        info.update({"altitude": alt, "azimuth": az, "above-horizon": alt > 0})
    return info


def object_info_html(info: Dict[str, Any]) -> str:
    """Stellarium answers format=html (the default) with an HTML fragment."""
    rows = "".join(
        f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in info.items()
    )
    return f"<h2>{html.escape(str(info.get('name', '')))}</h2><table>{rows}</table>"


def view(config: Dict[str, Any], selection: Dict[str, Any], now: Optional[datetime] = None):
    """View vectors pointing at the selected target (the emulated sky is always
    centred on it)."""
    now = now or datetime.now(timezone.utc)
    target = selection["target"]
    ra, dec = float(target["ra"]), float(target["dec"])
    ra_now, dec_now = astro.precess_from_j2000(ra, dec, astro.julian_date(now))
    if config["latitude"] is not None and config["longitude"] is not None:
        alt, az = astro.alt_az(ra, dec, config["latitude"], config["longitude"], now)
    else:
        alt, az = 0.0, 0.0
    return {
        "altAz": _vector_string(astro.stellarium_altaz_vector(alt, az)),
        "j2000": _vector_string(astro.unit_vector(ra, dec)),
        "jNow": _vector_string(astro.unit_vector(ra_now, dec_now)),
    }


def _prop(value: Any, type_name: str) -> Dict[str, Any]:
    return {"value": value, "variantType": type_name, "typeString": type_name,
            "typeEnum": {"bool": 1, "int": 2, "double": 6}.get(type_name, 10),
            "isWritable": True, "canNotify": True}


def properties(selection: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rotation = selection.get("rotation") if selection else None
    ccd_enabled = rotation is not None
    angle = ocular_angle_from_position_angle(float(rotation)) if ccd_enabled else 0.0
    return {
        "Oculars.enableOcular": _prop(False, "bool"),
        "Oculars.enableCCD": _prop(ccd_enabled, "bool"),
        "Oculars.selectedCCDRotationAngle": _prop(angle, "double"),
        "StelMovementMgr.currentFov": _prop(FOV_DEG, "double"),
    }
