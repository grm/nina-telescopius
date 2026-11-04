"""Configuration and persisted state (current selection)."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

NAME_FORMATS = ("name", "id", "both")

DEFAULTS: Dict[str, Any] = {
    "api_key": "",
    # "localhost" listens on both 127.0.0.1 and ::1 (NINA's default host is
    # "localhost", which Windows may resolve to ::1 first).
    "host": "localhost",
    # Stellarium Remote Control's default port, which is also NINA's default.
    "port": 8090,
    "latitude": None,
    "longitude": None,
    "elevation": 0.0,
    "location_name": "",
    # How the target name is sent to NINA (used in file names / sequence).
    "name_format": "name",
    "cache_ttl_seconds": 600,
}

ENV_API_KEY = "TELESCOPIUS_API_KEY"


class ConfigError(ValueError):
    pass


def _validate(values: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(values)
    for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
        v = out.get(key)
        if v in (None, ""):
            out[key] = None
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} doit être un nombre")
        if not low <= v <= high:
            raise ConfigError(f"{key} doit être entre {low} et {high}")
        out[key] = v
    try:
        out["elevation"] = float(out.get("elevation") or 0.0)
        out["port"] = int(out["port"])
        out["cache_ttl_seconds"] = int(out["cache_ttl_seconds"])
    except (TypeError, ValueError):
        raise ConfigError("elevation, port et cache_ttl_seconds doivent être numériques")
    if out.get("name_format") not in NAME_FORMATS:
        raise ConfigError(f"name_format doit être l'un de {', '.join(NAME_FORMATS)}")
    out["api_key"] = str(out.get("api_key") or "").strip()
    out["location_name"] = str(out.get("location_name") or "")
    return out


class Store:
    """Thread-safe holder for the config file and the selection state file."""

    EDITABLE = ("latitude", "longitude", "elevation", "location_name", "name_format", "api_key")

    def __init__(self, config_path: Path, state_path: Optional[Path] = None):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path) if state_path else self.config_path.with_name("state.json")
        self._lock = threading.RLock()
        self._config = self._load_config()
        self._selection = self._load_selection()

    # -- config -----------------------------------------------------------
    def _load_config(self) -> Dict[str, Any]:
        values = deepcopy(DEFAULTS)
        if self.config_path.exists():
            with self.config_path.open(encoding="utf-8") as f:
                values.update(json.load(f))
        env_key = os.environ.get(ENV_API_KEY)
        if env_key:
            values["api_key"] = env_key
        return _validate(values)

    @property
    def config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._config)

    def update_config(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        unknown = set(changes) - set(self.EDITABLE)
        if unknown:
            raise ConfigError(f"champs non modifiables : {', '.join(sorted(unknown))}")
        with self._lock:
            merged = _validate({**self._config, **changes})
            self._config = merged
            on_disk: Dict[str, Any] = {}
            if self.config_path.exists():
                with self.config_path.open(encoding="utf-8") as f:
                    on_disk = json.load(f)
            on_disk.update({k: merged[k] for k in changes})
            _atomic_write_json(self.config_path, on_disk)
            return dict(merged)

    def has_location(self) -> bool:
        c = self.config
        return c["latitude"] is not None and c["longitude"] is not None

    # -- selection --------------------------------------------------------
    def _load_selection(self) -> Optional[Dict[str, Any]]:
        if not self.state_path.exists():
            return None
        try:
            with self.state_path.open(encoding="utf-8") as f:
                return json.load(f).get("selection")
        except (OSError, ValueError):
            return None

    @property
    def selection(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._selection)

    def set_selection(self, selection: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            self._selection = deepcopy(selection)
            _atomic_write_json(self.state_path, {"selection": self._selection})


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
