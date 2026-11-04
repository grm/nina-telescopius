"""HTTP server: Stellarium emulation for NINA (/api/...) plus the web UI
(/ and /ui/api/...) used to pick the target NINA will import."""

from __future__ import annotations

import json
import logging
import math
import os
import socket
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from . import __version__, astro, stellarium
from .config import ConfigError, Store
from .telescopius import TelescopiusClient, TelescopiusError, nina_name

log = logging.getLogger("bridge")

STATIC_DIR = Path(__file__).with_name("static")
# Not using `mimetypes`: on Windows it reads the registry, which sometimes maps .js to text/plain.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
MAX_BODY = 64 * 1024

# Fields of a normalized target accepted back from the UI on selection.
_TARGET_STR_FIELDS = ("key", "id", "common_name", "family", "type_label", "constellation",
                      "thumbnail_url", "image_url", "page_url", "notes", "source")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _clean_target(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(400, "target manquant")
    try:
        ra = float(raw["ra"])
        dec = float(raw["dec"])
    except (KeyError, TypeError, ValueError):
        raise ApiError(400, "ra/dec invalides")
    if not (math.isfinite(ra) and math.isfinite(dec) and -90.0 <= dec <= 90.0):
        raise ApiError(400, "ra/dec hors limites")
    target: Dict[str, Any] = {"ra": ra % 360.0, "dec": dec, "epoch": "J2000"}
    for key in _TARGET_STR_FIELDS:
        value = raw.get(key)
        target[key] = str(value)[:500] if value is not None else None
    for key in ("mag", "size_arcmin", "position_angle"):
        try:
            target[key] = float(raw[key]) if raw.get(key) is not None else None
        except (TypeError, ValueError):
            target[key] = None
    target["aliases"] = [str(a)[:100] for a in (raw.get("aliases") or [])][:30]
    target["types"] = [str(a)[:30] for a in (raw.get("types") or [])][:30]
    target["moving"] = bool(raw.get("moving"))
    if not (target["id"] or target["common_name"]):
        raise ApiError(400, "la cible doit avoir un nom")
    return target


def _clean_rotation(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        rotation = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, "rotation invalide")
    if not math.isfinite(rotation):
        raise ApiError(400, "rotation invalide")
    return rotation % 360.0


class App:
    """Shared state for all request handlers."""

    def __init__(self, store: Store, client: Optional[TelescopiusClient] = None):
        self.store = store
        self.client = client or TelescopiusClient(
            api_key_provider=lambda: store.config["api_key"],
            cache_ttl_provider=lambda: store.config["cache_ttl_seconds"],
        )
        self.nina_activity: Deque[Dict[str, Any]] = deque(maxlen=20)
        self._activity_lock = threading.Lock()

    def note_nina(self, path: str, status: int) -> None:
        with self._activity_lock:
            self.nina_activity.appendleft({
                "time": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "path": path,
                "status": status,
            })

    def selection_view(self) -> Optional[Dict[str, Any]]:
        selection = self.store.selection
        if not selection:
            return None
        selection["nina_name"] = nina_name(selection["target"], self.store.config["name_format"])
        return selection

    def public_state(self) -> Dict[str, Any]:
        config = self.store.config
        key = config.pop("api_key")
        config["has_api_key"] = bool(key)
        config["api_key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
        with self._activity_lock:
            activity = list(self.nina_activity)
        return {
            "version": __version__,
            "config": config,
            "selection": self.selection_view(),
            "nina_activity": activity,
            "rate_limit_remaining": self.client.rate_limit_remaining,
        }

    def location(self, tz: Optional[str]) -> Dict[str, Any]:
        config = self.store.config
        return {"latitude": config["latitude"], "longitude": config["longitude"], "timezone": tz}

    def add_visibility(self, targets: List[Dict[str, Any]]) -> None:
        config = self.store.config
        if config["latitude"] is None or config["longitude"] is None:
            return
        now = datetime.now(timezone.utc)
        for t in targets:
            t["visibility"] = astro.visibility_summary(t["ra"], t["dec"], config["latitude"],
                                                       config["longitude"], now)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"nina-telescopius/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- plumbing -----------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # silence default stderr log
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, data: Any) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _text(self, status: int, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
            self._send(status, text.encode("utf-8"), content_type)

        def _body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, "requête trop volumineuse")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:
                raise ApiError(400, "JSON invalide")

        def _dispatch(self) -> None:
            url = urlsplit(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path.startswith("/api/"):
                    self._stellarium(url.path, query)
                elif url.path.startswith("/ui/api/"):
                    self._ui_api(url.path, query)
                else:
                    self._static(url.path)
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except TelescopiusError as e:
                self._json(e.status, {"error": str(e)})
            except ConfigError as e:
                self._json(400, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                log.exception("Erreur sur %s %s", self.command, self.path)
                self._json(500, {"error": "erreur interne, voir la console du bridge"})

        def do_GET(self) -> None:
            self._dispatch()

        def do_HEAD(self) -> None:
            self._dispatch()

        def do_POST(self) -> None:
            self._dispatch()

        def do_DELETE(self) -> None:
            self._dispatch()

        # -- Stellarium emulation -----------------------------------------
        def _stellarium(self, path: str, query: Dict[str, str]) -> None:
            status, kind, payload = self._stellarium_response(path, query)
            app.note_nina(path, status)
            log.info("NINA %s %s -> %d", self.command, self.path, status)
            if kind == "json":
                self._json(status, payload)
            elif kind == "html":
                self._text(status, payload, "text/html; charset=utf-8")
            else:
                self._text(status, payload)

        def _stellarium_response(self, path: str, query: Dict[str, str]) -> Tuple[int, str, Any]:
            if self.command not in ("GET", "HEAD"):
                return 405, "text", "Method not allowed"
            config = app.store.config
            selection = app.store.selection

            if path == "/api/main/status":
                if config["latitude"] is None or config["longitude"] is None:
                    return 503, "text", "Location not configured in the Telescopius bridge"
                return 200, "json", stellarium.status(config, selection)

            if path == "/api/objects/info":
                if not selection:
                    # Same status as Stellarium when nothing is selected.
                    return 404, "text", "no current selection, and no name parameter provided"
                info = stellarium.object_info(config, selection)
                if query.get("format", "html") == "json":
                    return 200, "json", info
                return 200, "html", stellarium.object_info_html(info)

            if path == "/api/main/view":
                if not selection:
                    # NINA falls back to the view centre when objects/info is 404;
                    # failing here too makes NINA report an error instead of
                    # importing meaningless coordinates.
                    return 404, "text", "no target selected in the Telescopius bridge"
                return 200, "json", stellarium.view(config, selection)

            if path == "/api/stelproperty/list":
                return 200, "json", stellarium.properties(selection)

            return 404, "text", "Not found"

        # -- UI API ---------------------------------------------------------
        def _ui_api(self, path: str, query: Dict[str, str]) -> None:
            method = self.command
            refresh = query.pop("refresh", "") in ("1", "true")
            tz = query.pop("tz", None)

            if path == "/ui/api/state" and method == "GET":
                return self._json(200, app.public_state())

            if path == "/ui/api/config" and method == "POST":
                app.store.update_config(self._body())
                return self._json(200, app.public_state())

            if path == "/ui/api/highlights" and method == "GET":
                return self._json(200, app.client.highlights(query, app.location(tz), refresh))

            if path == "/ui/api/search" and method == "GET":
                return self._json(200, app.client.search(query, app.location(tz), refresh))

            if path == "/ui/api/lists" and method == "GET":
                return self._json(200, {"lists": app.client.lists(refresh)})

            if path.startswith("/ui/api/lists/") and method == "GET":
                result = app.client.target_list(path[len("/ui/api/lists/"):], refresh)
                app.add_visibility(result["targets"])
                return self._json(200, result)

            if path == "/ui/api/selection":
                if method == "POST":
                    body = self._body()
                    target = _clean_target(body.get("target"))
                    rotation = _clean_rotation(body.get("rotation"))
                    app.store.set_selection({
                        "target": target,
                        "rotation": rotation,
                        "selected_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    })
                    log.info("Cible sélectionnée : %s (RA %.4f°, Dec %.4f°, rotation %s)",
                             target.get("id") or target.get("common_name"), target["ra"], target["dec"],
                             "—" if rotation is None else f"{rotation:.2f}°")
                    return self._json(200, app.public_state())
                if method == "DELETE":
                    app.store.set_selection(None)
                    return self._json(200, app.public_state())

            if path == "/ui/api/selection/rotation" and method == "POST":
                selection = app.store.selection
                if not selection:
                    raise ApiError(409, "aucune cible sélectionnée")
                selection["rotation"] = _clean_rotation(self._body().get("rotation"))
                app.store.set_selection(selection)
                return self._json(200, app.public_state())

            raise ApiError(404, "route inconnue")

        # -- static files ---------------------------------------------------
        def _static(self, path: str) -> None:
            if self.command not in ("GET", "HEAD"):
                raise ApiError(405, "méthode non autorisée")
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            if rel.startswith("static/"):
                rel = rel[len("static/"):]
            file = (STATIC_DIR / rel).resolve()
            if STATIC_DIR.resolve() not in file.parents or not file.is_file():
                return self._text(404, "Not found")
            content_type = CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream")
            self._send(200, file.read_bytes(), content_type)

    return Handler


class _V6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        if self.server_address[0] == "::":
            try:  # dual stack: also accept IPv4 connections
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        super().server_bind()


class _V4Server(ThreadingHTTPServer):
    address_family = socket.AF_INET


def _handle_error(self, request, client_address) -> None:
    # Clients such as NINA drop keep-alive connections abruptly; not worth a traceback.
    if isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return
    log.exception("Erreur de connexion avec %s", client_address)


for _cls in (_V4Server, _V6Server):
    _cls.daemon_threads = True
    # On Windows SO_REUSEADDR would let us share the port with a running Stellarium
    # instead of failing; elsewhere it only avoids TIME_WAIT errors on restart.
    _cls.allow_reuse_address = os.name != "nt"
    _cls.handle_error = _handle_error


def create_servers(app: App, host: str, port: int) -> List[ThreadingHTTPServer]:
    """`localhost` binds 127.0.0.1 and ::1; `0.0.0.0`/`::`/`*` bind every
    interface (dual stack when possible); anything else is used as is."""
    handler = make_handler(app)
    if host == "localhost":
        servers: List[ThreadingHTTPServer] = [_V4Server(("127.0.0.1", port), handler)]
        if socket.has_ipv6:
            try:
                servers.append(_V6Server(("::1", servers[0].server_address[1]), handler))
            except OSError as e:
                log.debug("IPv6 loopback indisponible : %s", e)
        return servers
    if host in ("0.0.0.0", "::", "*"):
        if socket.has_ipv6:
            try:
                return [_V6Server(("::", port), handler)]
            except OSError:
                pass
        return [_V4Server(("0.0.0.0", port), handler)]
    if ":" in host:
        return [_V6Server((host, port), handler)]
    return [_V4Server((host, port), handler)]


def serve(servers: List[ThreadingHTTPServer]) -> List[threading.Thread]:
    threads = []
    for srv in servers:
        t = threading.Thread(target=srv.serve_forever, name=f"http-{srv.server_address[0]}", daemon=True)
        t.start()
        threads.append(t)
    return threads
