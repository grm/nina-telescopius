import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from telescopius_bridge.telescopius import (
    TelescopiusClient, TelescopiusError, nina_name, normalize_list_target,
    normalize_search_item, type_label,
)

FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class NormalizationTest(unittest.TestCase):
    def test_deep_sky_object(self):
        t = normalize_search_item(fixture("highlights.json")["page_results"][0], "x")
        self.assertEqual(t["id"], "IC 1805")
        self.assertEqual(t["common_name"], "Heart Nebula")
        self.assertAlmostEqual(t["ra"], 2.547333 * 15)
        self.assertAlmostEqual(t["dec"], 61.471111)
        self.assertFalse(t["moving"])
        self.assertEqual(t["size_arcmin"], 150.0)  # major_axis is in arcsec
        self.assertEqual(t["type_label"], "Nébuleuse en émission")
        self.assertEqual(t["tonight"]["transit"], "00:27")
        self.assertNotIn("IC 1805", t["aliases"])

    def test_solar_system_object_uses_current_position(self):
        item = fixture("search_jupiter.json")["page_results"][0]
        t = normalize_search_item(item, "x")
        self.assertTrue(t["moving"])
        self.assertAlmostEqual(t["ra"], item["current_observation"]["ra"] * 15)
        self.assertAlmostEqual(t["dec"], item["current_observation"]["dec"])
        self.assertEqual(t["type_label"], "Planète")

    def test_solar_system_falls_back_to_transit_position(self):
        item = fixture("search_jupiter.json")["page_results"][0]
        del item["current_observation"]
        t = normalize_search_item(item, "x")
        self.assertAlmostEqual(t["ra"], item["transit_observation"]["ra"] * 15)

    def test_list_targets(self):
        data = fixture("target_list.json")
        targets = [normalize_list_target(t, data["name"]) for t in data["targets"]]
        trunk, pane, broken = targets
        self.assertIsNone(broken)
        self.assertEqual((trunk["id"], trunk["common_name"]), ("IC 1396", "Elephant Trunk"))
        self.assertAlmostEqual(trunk["ra"], 21.64966774 * 15)  # J2000, not ra_hr_jnow
        self.assertIsNone(trunk["position_angle"])
        self.assertEqual(pane["id"], "")
        self.assertEqual(pane["common_name"], "Orion - pane 1 of 2")
        self.assertEqual(pane["position_angle"], 12.5)
        self.assertEqual(pane["size_arcmin"], 174.0)
        self.assertNotEqual(trunk["key"], pane["key"])

    def test_nina_name(self):
        t = {"id": "IC 1805", "common_name": "Heart Nebula"}
        self.assertEqual(nina_name(t, "name"), "Heart Nebula")
        self.assertEqual(nina_name(t, "id"), "IC 1805")
        self.assertEqual(nina_name(t, "both"), "IC 1805 Heart Nebula")
        self.assertEqual(nina_name({"id": "M 31", "common_name": ""}, "name"), "M 31")
        self.assertEqual(nina_name({"id": "", "common_name": "pane 1"}, "id"), "pane 1")

    def test_type_label(self):
        self.assertEqual(type_label(["sgx", "gxy", "deep_sky_object"]), "Galaxie spirale")
        self.assertEqual(type_label(["deep_sky_object", "weird"]), "weird")
        self.assertEqual(type_label([]), "Objet")


class _FakeApi(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        _FakeApi.calls.append((self.path, self.headers.get("Authorization")))
        if self.path.startswith("/fail401"):
            return self._reply(401, {"error": {"status": 401, "message": "Unauthorized"}})
        if self.path.startswith("/fail429"):
            return self._reply(429, {"error": {"status": 429, "message": "Too many"}}, {"x-ratelimit-remaining": "0"})
        if self.path.startswith("/targets/search"):
            return self._reply(200, fixture("highlights.json"), {"x-ratelimit-remaining": "42"})
        return self._reply(404, {"error": {"status": 404, "message": "nope"}})

    def _reply(self, status, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _FakeApi)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        _FakeApi.calls.clear()
        self.client = TelescopiusClient(lambda: "secret", base_url=self.base)

    def test_search_sends_key_location_and_caches(self):
        loc = {"latitude": 48.8, "longitude": 2.3, "timezone": "Europe/Paris"}
        out = self.client.search({"name": "m31", "evil": "x"}, loc)
        self.assertEqual(len(out["targets"]), 2)
        self.client.search({"name": "m31"}, loc)
        self.assertEqual(len(_FakeApi.calls), 1, "second call should be served from cache")
        path, auth = _FakeApi.calls[0]
        self.assertEqual(auth, "Key secret")
        for part in ("lat=48.8", "lon=2.3", "timezone=Europe%2FParis", "name=m31", "compute_current=1"):
            self.assertIn(part, path)
        self.assertNotIn("evil", path)
        self.assertEqual(self.client.rate_limit_remaining, 42)
        self.client.search({"name": "m31"}, loc, refresh=True)
        self.assertEqual(len(_FakeApi.calls), 2)

    def test_bad_timezone_is_dropped(self):
        self.client.search({}, {"latitude": None, "longitude": None, "timezone": "x&y=1"})
        self.assertNotIn("timezone", _FakeApi.calls[0][0])
        self.assertNotIn("lat=", _FakeApi.calls[0][0])

    def test_errors(self):
        with self.assertRaises(TelescopiusError) as e:
            self.client.get("/fail401")
        self.assertEqual(e.exception.status, 401)
        with self.assertRaises(TelescopiusError) as e:
            self.client.get("/fail429")
        self.assertEqual(e.exception.status, 429)
        self.assertEqual(self.client.rate_limit_remaining, 0)
        with self.assertRaises(TelescopiusError) as e:
            TelescopiusClient(lambda: "", base_url=self.base).get("/targets/search")
        self.assertEqual(e.exception.status, 400)

    def test_network_error(self):
        client = TelescopiusClient(lambda: "k", base_url="http://127.0.0.1:9", timeout=2)
        with self.assertRaises(TelescopiusError) as e:
            client.get("/x")
        self.assertEqual(e.exception.status, 502)

    def test_invalid_list_id(self):
        with self.assertRaises(TelescopiusError):
            self.client.target_list("../etc")


if __name__ == "__main__":
    unittest.main()
