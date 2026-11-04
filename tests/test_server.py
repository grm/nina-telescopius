import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from telescopius_bridge.config import Store
from telescopius_bridge.server import App, create_servers, serve
from telescopius_bridge.telescopius import normalize_list_target, normalize_search_item

FIXTURES = Path(__file__).with_name("fixtures")
COMPAT_DIR = Path(__file__).with_name("nina_compat")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    rate_limit_remaining = 50

    def __init__(self):
        self.calls = []

    def highlights(self, filters, location, refresh=False):
        self.calls.append(("highlights", filters, location))
        items = fixture("highlights.json")["page_results"]
        return {"matched": 2, "targets": [normalize_search_item(i, "h") for i in items]}

    search = highlights

    def lists(self, refresh=False):
        return [{"id": "abc12345", "name": "Test list", "count": 3}]

    def target_list(self, list_id, refresh=False):
        data = fixture("target_list.json")
        targets = [t for t in (normalize_list_target(t, data["name"]) for t in data["targets"]) if t]
        return {"id": list_id, "name": data["name"], "targets": targets}


class ServerFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = Path(self.tmp.name) / "config.json"
        config.write_text(json.dumps({
            "api_key": "secret-key-1234", "latitude": 48.8566, "longitude": 2.3522,
            "elevation": 35, "location_name": "Paris",
        }))
        self.store = Store(config)
        self.client = FakeClient()
        self.servers = create_servers(App(self.store, self.client), "localhost", 0)
        serve(self.servers)
        self.port = self.servers[0].server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.tmp.cleanup()

    def request(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw, status, ctype = r.read(), r.status, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            raw, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
        if "json" in ctype:
            return status, json.loads(raw)
        return status, raw.decode()

    def select_heart(self, rotation=None):
        _, data = self.request("/ui/api/highlights?tz=Europe/Paris")
        heart = data["targets"][0]
        status, state = self.request("/ui/api/selection", "POST", {"target": heart, "rotation": rotation})
        self.assertEqual(status, 200)
        return heart, state


class BridgeServerTest(ServerFixture):
    # -- Stellarium emulation ---------------------------------------------
    def test_no_selection(self):
        self.assertEqual(self.request("/api/objects/info?format=json")[0], 404)
        self.assertEqual(self.request("/api/main/view")[0], 404)
        status, props = self.request("/api/stelproperty/list?format=json")
        self.assertEqual(status, 200)
        self.assertIs(props["Oculars.enableCCD"]["value"], False)

    def test_status_location(self):
        status, data = self.request("/api/main/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["location"]["latitude"], 48.8566)
        self.assertEqual(data["location"]["longitude"], 2.3522)
        self.assertEqual(data["location"]["altitude"], 35)

    def test_status_without_location(self):
        self.store.update_config({"latitude": None, "longitude": None})
        self.assertEqual(self.request("/api/main/status")[0], 503)

    def test_object_info_for_selection(self):
        heart, _ = self.select_heart()
        status, info = self.request("/api/objects/info?format=json")
        self.assertEqual(status, 200)
        self.assertEqual(info["localized-name"], "Heart Nebula")
        self.assertAlmostEqual(info["raJ2000"], heart["ra"])
        self.assertAlmostEqual(info["decJ2000"], heart["dec"])
        self.assertTrue(info["designations"].startswith("IC 1805 - "))
        self.assertIn("altitude", info)
        status, html = self.request("/api/objects/info")
        self.assertEqual(status, 200)
        self.assertIn("<h2>Heart Nebula</h2>", html)

    def test_name_format_applies_to_existing_selection(self):
        self.select_heart()
        self.store.update_config({"name_format": "both"})
        _, info = self.request("/api/objects/info?format=json")
        self.assertEqual(info["name"], "IC 1805 Heart Nebula")

    def test_rotation_enables_ccd_and_view(self):
        self.select_heart(rotation=30)
        _, props = self.request("/api/stelproperty/list?format=json")
        self.assertIs(props["Oculars.enableCCD"]["value"], True)
        self.assertAlmostEqual(props["Oculars.selectedCCDRotationAngle"]["value"], 330.0)
        status, view = self.request("/api/main/view")
        self.assertEqual(status, 200)
        self.assertTrue(view["j2000"].startswith("[") and view["j2000"].endswith("]"))
        status, state = self.request("/ui/api/selection/rotation", "POST", {"rotation": None})
        self.assertIsNone(state["selection"]["rotation"])
        _, props = self.request("/api/stelproperty/list?format=json")
        self.assertIs(props["Oculars.enableCCD"]["value"], False)

    def test_unknown_and_post_routes(self):
        self.assertEqual(self.request("/api/main/focus", "POST", {})[0], 405)
        self.assertEqual(self.request("/api/scripts/list")[0], 404)

    def test_nina_activity_recorded(self):
        self.request("/api/objects/info?format=json")
        _, state = self.request("/ui/api/state")
        self.assertEqual(state["nina_activity"][0]["path"], "/api/objects/info")
        self.assertEqual(state["nina_activity"][0]["status"], 404)

    # -- UI API --------------------------------------------------------------
    def test_state_hides_api_key(self):
        _, state = self.request("/ui/api/state")
        self.assertNotIn("api_key", state["config"])
        self.assertTrue(state["config"]["has_api_key"])
        self.assertEqual(state["config"]["api_key_hint"], "…1234")
        self.assertNotIn("secret-key-1234", json.dumps(state))

    def test_highlights_passes_browser_timezone(self):
        self.request("/ui/api/highlights?tz=Europe/Paris&min_alt=30")
        _, filters, location = self.client.calls[0]
        self.assertEqual(location, {"latitude": 48.8566, "longitude": 2.3522, "timezone": "Europe/Paris"})
        self.assertEqual(filters, {"min_alt": "30"})

    def test_list_targets_get_visibility(self):
        status, data = self.request("/ui/api/lists/abc12345?tz=UTC")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["targets"]), 2)
        self.assertIn("alt_now", data["targets"][0]["visibility"])

    def test_selection_persists_and_clears(self):
        self.select_heart(rotation=12.5)
        reloaded = Store(self.store.config_path)
        self.assertEqual(reloaded.selection["target"]["id"], "IC 1805")
        self.assertEqual(reloaded.selection["rotation"], 12.5)
        status, state = self.request("/ui/api/selection", "DELETE")
        self.assertIsNone(state["selection"])
        self.assertIsNone(Store(self.store.config_path).selection)

    def test_selection_validation(self):
        bad = [
            {"target": {"id": "x", "ra": "abc", "dec": 0}},
            {"target": {"id": "x", "ra": 10, "dec": 95}},
            {"target": {"ra": 10, "dec": 5}},
            {"target": {"id": "x", "ra": 10, "dec": 5}, "rotation": "nan"},
            {},
        ]
        for body in bad:
            self.assertEqual(self.request("/ui/api/selection", "POST", body)[0], 400, body)

    def test_config_update(self):
        status, state = self.request("/ui/api/config", "POST", {"latitude": "45.5", "longitude": -73.6})
        self.assertEqual(status, 200)
        self.assertEqual(state["config"]["latitude"], 45.5)
        on_disk = json.loads(self.store.config_path.read_text())
        self.assertEqual(on_disk["latitude"], 45.5)
        self.assertEqual(on_disk["api_key"], "secret-key-1234")
        self.assertEqual(self.request("/ui/api/config", "POST", {"latitude": 123})[0], 400)
        self.assertEqual(self.request("/ui/api/config", "POST", {"port": 1})[0], 400)

    def test_static_files(self):
        status, html = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Telescopius", html)
        self.assertEqual(self.request("/static/app.js")[0], 200)
        self.assertEqual(self.request("/static/../config.py")[0], 404)
        self.assertEqual(self.request("/static/%2e%2e/config.py")[0], 404)

    def test_ipv6_loopback(self):
        if len(self.servers) < 2:
            self.skipTest("no IPv6 loopback")
        with urllib.request.urlopen(f"http://[::1]:{self.port}/api/stelproperty/list", timeout=5) as r:
            self.assertEqual(r.status, 200)


def _dotnet():
    for candidate in (os.environ.get("DOTNET"), shutil.which("dotnet"), "/tmp/dotnet/dotnet"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


@unittest.skipUnless(_dotnet() and (COMPAT_DIR / "vendor" / "Stellarium.cs").exists(),
                     "needs the .NET 8 SDK and tests/nina_compat/fetch_nina_sources.sh")
class NinaDriverCompatTest(ServerFixture):
    """Runs NINA's own Stellarium driver (C#) against the bridge."""

    @classmethod
    def setUpClass(cls):
        dll = COMPAT_DIR / "bin" / "Release" / "net8.0" / "NinaCompat.dll"
        subprocess.run([_dotnet(), "build", "-c", "Release", "-nologo", "-v", "q"], cwd=COMPAT_DIR,
                       check=True, capture_output=True)
        cls.dll = dll

    def nina(self, host="localhost"):
        out = subprocess.run([_dotnet(), str(self.dll), host, str(self.port)], capture_output=True,
                             text=True, timeout=60, check=True)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_nina_without_selection_reports_error(self):
        result = self.nina()
        self.assertEqual(result["site"], {"Latitude": 48.8566, "Longitude": 2.3522, "Elevation": 35.0})
        self.assertIn("targetError", result)

    def test_nina_imports_target(self):
        heart, _ = self.select_heart()
        result = self.nina()
        self.assertEqual(result["target"]["Name"], "Heart Nebula")
        self.assertEqual(result["target"]["Epoch"], "J2000")
        self.assertAlmostEqual(result["target"]["RADegrees"], heart["ra"], places=6)
        self.assertAlmostEqual(result["target"]["Dec"], heart["dec"], places=6)
        self.assertIsNone(result["rotationAngle"])  # framing keeps its current rotation
        self.assertEqual(result["sequencerPositionAngle"], 0.0)

    def test_nina_imports_rotation(self):
        heart, _ = self.select_heart(rotation=37.5)
        for host in ("localhost", "127.0.0.1"):
            result = self.nina(host)
            self.assertEqual(result["target"]["Name"], "Heart Nebula")
            self.assertAlmostEqual(result["target"]["RADegrees"], heart["ra"], places=6)
            self.assertAlmostEqual(result["target"]["Dec"], heart["dec"], places=6)
            self.assertAlmostEqual(result["rotationAngle"], 37.5, places=6)
            self.assertAlmostEqual(result["sequencerPositionAngle"], 37.5, places=6)
            self.assertAlmostEqual(result["framingRotation"], 322.5, places=6)

    def test_nina_imports_list_target_near_ra_zero(self):
        _, data = self.request("/ui/api/lists/abc12345")
        target = dict(data["targets"][0], ra=359.9999, dec=-89.5)
        self.request("/ui/api/selection", "POST", {"target": target, "rotation": 359.9})
        result = self.nina()
        self.assertAlmostEqual(result["target"]["RADegrees"], 359.9999, places=5)
        self.assertAlmostEqual(result["target"]["Dec"], -89.5, places=6)
        self.assertAlmostEqual(result["rotationAngle"], 359.9, places=6)


if __name__ == "__main__":
    unittest.main()
