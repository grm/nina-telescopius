"""Telescopius REST API client and result normalization.

Every target coming out of this module uses the same shape (see `_target`),
with J2000 coordinates in degrees, whatever endpoint it came from.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import __version__

BASE_URL = "https://api.telescopius.com/v2.2"
USER_AGENT = f"nina-telescopius-bridge/{__version__}"

# Query parameters the UI may forward to /targets/search.
SEARCH_PARAMS = (
    "name", "name_exact", "types", "cat", "con", "min_alt", "min_alt_minutes",
    "moon_dist_min", "mag_max", "size_min", "size_max", "order", "order_asc",
    "results_per_page", "page", "datetime",
)
# results_per_page/page are undocumented for highlights but honoured (default is 10).
HIGHLIGHT_PARAMS = ("types", "min_alt", "min_alt_minutes", "moon_dist_min", "datetime",
                    "results_per_page", "page")

_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$")

# Most specific Telescopius type codes first; the first match is displayed.
TYPE_LABELS: Tuple[Tuple[str, str], ...] = (
    ("planet", "Planète"),
    ("moon", "Lune"),
    ("sun", "Soleil"),
    ("comet", "Comète"),
    ("asteroid", "Astéroïde"),
    ("pneb", "Nébuleuse planétaire"),
    ("snr", "Rémanent de supernova"),
    ("eneb", "Nébuleuse en émission"),
    ("rneb", "Nébuleuse par réflexion"),
    ("dkneb", "Nébuleuse obscure"),
    ("drkneb", "Nébuleuse obscure"),
    ("h2r", "Région HII"),
    ("glcl", "Amas globulaire"),
    ("opcl", "Amas ouvert"),
    ("gxcl", "Amas de galaxies"),
    ("gxgr", "Groupe de galaxies"),
    ("sgx", "Galaxie spirale"),
    ("egx", "Galaxie elliptique"),
    ("lgx", "Galaxie lenticulaire"),
    ("igx", "Galaxie irrégulière"),
    ("gxy", "Galaxie"),
    ("dineb", "Nébuleuse diffuse"),
    ("neb", "Nébuleuse"),
    ("stcl", "Amas d'étoiles"),
    ("ast", "Astérisme"),
    ("str", "Étoile"),
)
_TYPE_LABELS = dict(TYPE_LABELS)
_TYPE_ORDER = {code: i for i, (code, _) in enumerate(TYPE_LABELS)}


class TelescopiusError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def type_label(types: Iterable[str]) -> str:
    known = [t for t in types if t in _TYPE_ORDER]
    if known:
        return _TYPE_LABELS[min(known, key=_TYPE_ORDER.__getitem__)]
    for t in types:
        if t not in ("deep_sky_object", "solar_system_object", "ism"):
            return t
    return "Objet"


def nina_name(target: Dict[str, Any], name_format: str) -> str:
    """The name NINA will receive (it ends up in sequence and file names)."""
    ident = (target.get("id") or "").strip()
    common = (target.get("common_name") or "").strip()
    if name_format == "id":
        return ident or common
    if name_format == "both" and ident and common and common.lower() != ident.lower():
        return f"{ident} {common}"
    return common or ident


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _target(**fields: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "key": "",
        "id": "",
        "common_name": "",
        "aliases": [],
        "family": "deep_sky_object",
        "types": [],
        "type_label": "Objet",
        "ra": None,
        "dec": None,
        "epoch": "J2000",
        "moving": False,
        "mag": None,
        "size_arcmin": None,
        "constellation": "",
        "thumbnail_url": None,
        "image_url": None,
        "page_url": None,
        "position_angle": None,
        "notes": None,
        "tonight": None,
        "visibility": None,
        "source": "",
    }
    base.update(fields)
    return base


def _absolute_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url if url.startswith("http") else "https://telescopius.com" + url


def normalize_search_item(item: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    """One element of `page_results` from /targets/search or /targets/highlights."""
    obj = item.get("object") or {}
    family = obj.get("family") or "deep_sky_object"
    moving = family == "solar_system_object"

    ra_hours = _num(obj.get("ra"))
    dec = _num(obj.get("dec"))
    if ra_hours is None or dec is None or moving:
        # Solar system bodies have no fixed position: use the position at the
        # query time (compute_current=1), falling back to the one at transit.
        # Both are geocentric J2000, verified against astropy.
        obs = item.get("current_observation") or item.get("transit_observation") or {}
        ra_hours, dec = _num(obs.get("ra")), _num(obs.get("dec"))
    if ra_hours is None or dec is None:
        return None

    main_id = obj.get("main_id") or ""
    main_name = obj.get("main_name") or ""
    types = obj.get("types") or []
    major = _num(obj.get("major_axis"))
    mag = _num(obj.get("visual_mag"))
    if mag is None:
        mag = _num((item.get("transit_observation") or {}).get("mag"))

    tonight = None
    times = item.get("tonight_times")
    vis = item.get("tonight_visibility")
    if times or vis:
        windows = (vis or {}).get("windows") or []
        tonight = {
            "rise": (times or {}).get("rise"),
            "transit": (times or {}).get("transit"),
            "set": (times or {}).get("set"),
            "max_alt": (vis or {}).get("max_altitude"),
            "max_alt_time": (vis or {}).get("max_altitude_hour"),
            "imaging_hours": round(sum(_num(w.get("imaging_time_hours")) or 0 for w in windows), 2),
            "windows": windows,
            "moon_distance": min(
                (d for d in (_num(w.get("moon_distance_deg")) for w in windows) if d is not None),
                default=None,
            ),
            "moon_illumination": next(
                (_num(w.get("moon_illumination_percent")) for w in windows), None
            ),
        }

    return _target(
        key=f"obj:{main_id}",
        id=main_id,
        common_name=main_name if main_name and main_name != main_id else "",
        aliases=[i for i in (obj.get("ids") or []) + (obj.get("names") or []) if i not in (main_id, main_name)],
        family=family,
        types=types,
        type_label=type_label(types),
        ra=(ra_hours * 15.0) % 360.0,
        dec=dec,
        moving=moving,
        mag=mag,
        size_arcmin=round(major / 60.0, 1) if major else None,
        constellation=obj.get("con_name") or obj.get("con") or "",
        thumbnail_url=obj.get("thumbnail_url"),
        image_url=obj.get("main_image_url"),
        page_url=_absolute_url(obj.get("url")),
        tonight=tonight,
        source=source,
    )


def normalize_list_target(t: Dict[str, Any], list_name: str) -> Optional[Dict[str, Any]]:
    """One element of `targets` from /target-lists/{id}."""
    ra_hours, dec = _num(t.get("ra_hr")), _num(t.get("dec"))
    if ra_hours is None or dec is None:
        return None
    object_id = t.get("object_id") or ""
    name = t.get("name") or object_id
    size_deg = _num(t.get("size_deg"))
    return _target(
        key=f"list:{t.get('id')}",
        id=object_id,
        common_name=name if name != object_id else "",
        ra=(ra_hours * 15.0) % 360.0,
        dec=dec,
        mag=_num(t.get("visual_mag")),
        size_arcmin=round(size_deg * 60.0, 1) if size_deg else None,
        type_label="",  # saved list entries carry no object type
        constellation=t.get("constellation_id") or "",
        thumbnail_url=t.get("thumbnail_url"),
        position_angle=_num(t.get("position_angle_east")),
        notes=t.get("notes"),
        source=f"Liste : {list_name}",
    )


class TelescopiusClient:
    def __init__(self, api_key_provider, cache_ttl_provider=lambda: 600, base_url: str = BASE_URL,
                 timeout: float = 20.0):
        self._api_key = api_key_provider
        self._cache_ttl = cache_ttl_provider
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.rate_limit_remaining: Optional[int] = None

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, ttl: Optional[float] = None,
            refresh: bool = False) -> Any:
        api_key = self._api_key()
        if not api_key:
            raise TelescopiusError("Aucune clé API Telescopius configurée", status=400)
        query = urllib.parse.urlencode(sorted((k, v) for k, v in (params or {}).items() if v not in (None, "")))
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        ttl = self._cache_ttl() if ttl is None else ttl

        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(url)
            if hit and not refresh and now - hit[0] < ttl:
                return hit[1]

        request = urllib.request.Request(url, headers={
            "Authorization": f"Key {api_key}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._note_rate_limit(response.headers)
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._note_rate_limit(e.headers)
            raise TelescopiusError(self._error_message(e), status=e.code if e.code in (400, 401, 403, 404, 429) else 502)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TelescopiusError(f"Telescopius injoignable : {getattr(e, 'reason', e)}")
        except ValueError:
            raise TelescopiusError("Réponse Telescopius illisible")

        with self._lock:
            if len(self._cache) > 300:
                self._cache = {k: v for k, v in self._cache.items() if now - v[0] < ttl}
            self._cache[url] = (now, data)
        return data

    def _note_rate_limit(self, headers) -> None:
        if headers is None:
            return
        remaining = headers.get("x-ratelimit-remaining")
        if remaining is not None and str(remaining).isdigit():
            self.rate_limit_remaining = int(remaining)

    @staticmethod
    def _error_message(e: urllib.error.HTTPError) -> str:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or ""
        except Exception:
            pass
        if e.code == 401:
            return "Clé API Telescopius refusée (401)"
        if e.code == 429:
            return "Limite de requêtes Telescopius atteinte (60/min), réessaie dans une minute"
        return f"Erreur Telescopius {e.code}" + (f" : {detail}" if detail else "")

    # -- endpoints --------------------------------------------------------
    @staticmethod
    def _location_params(location: Dict[str, Any]) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if location.get("latitude") is not None and location.get("longitude") is not None:
            params["lat"] = location["latitude"]
            params["lon"] = location["longitude"]
        tz = location.get("timezone")
        if tz and _TZ_RE.match(tz):
            params["timezone"] = tz
        return params

    def _search_like(self, path: str, allowed: Iterable[str], filters: Dict[str, Any],
                     location: Dict[str, Any], source: str, refresh: bool) -> Dict[str, Any]:
        params = self._location_params(location)
        params.update({k: v for k, v in filters.items() if k in allowed})
        params["compute_current"] = 1
        data = self.get(path, params, refresh=refresh)
        items = data.get("page_results") or data.get("objects") or []
        targets = [t for t in (normalize_search_item(i, source) for i in items) if t]
        return {"matched": data.get("matched", len(targets)), "targets": targets}

    def highlights(self, filters: Dict[str, Any], location: Dict[str, Any], refresh: bool = False):
        return self._search_like("/targets/highlights", HIGHLIGHT_PARAMS, filters, location,
                                 "Incontournables du moment", refresh)

    def search(self, filters: Dict[str, Any], location: Dict[str, Any], refresh: bool = False):
        return self._search_like("/targets/search", SEARCH_PARAMS, filters, location, "Recherche", refresh)

    def lists(self, refresh: bool = False) -> List[Dict[str, Any]]:
        data = self.get("/target-lists", ttl=60, refresh=refresh)
        return [
            {"id": l.get("id"), "name": l.get("name"), "count": l.get("targets_count")}
            for l in (data.get("lists") or [])
        ]

    def target_list(self, list_id: str, refresh: bool = False) -> Dict[str, Any]:
        if not re.match(r"^[A-Za-z0-9_-]+$", list_id or ""):
            raise TelescopiusError("Identifiant de liste invalide", status=400)
        data = self.get(f"/target-lists/{list_id}", ttl=60, refresh=refresh)
        name = data.get("name") or list_id
        targets = [t for t in (normalize_list_target(t, name) for t in (data.get("targets") or [])) if t]
        return {"id": data.get("id", list_id), "name": name, "targets": targets}
