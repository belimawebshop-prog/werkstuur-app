import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from legacy_redirect import RedirectHandler


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LegacyRedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.client = urllib.request.build_opener(NoRedirect())

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}" + path, method=method)
        try:
            return self.client.open(req)
        except urllib.error.HTTPError as exc:
            return exc

    def test_browser_and_existing_intake_links_use_current_portal(self):
        for path, target in (("/", "https://portal.werkstuur.nl/"),
                             ("/intake.html?token=synthetic", "https://portal.werkstuur.nl/intake.html?token=synthetic"),
                             ("//evil.invalid/?target=evil", "https://portal.werkstuur.nl/")):
            response = self.request(path)
            self.assertEqual(response.status, 302)
            self.assertEqual(response.headers["Location"], target)
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
            response.close()

    def test_old_api_never_forwards_authentication_or_writes(self):
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"):
            response = self.request("/api/cases", method)
            self.assertEqual(response.status, 410)
            self.assertNotIn("Location", response.headers)
            response.close()

    def test_health_is_local_and_head_has_no_body(self):
        response = self.request("/api/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(json.load(response)["version"], "2.1.2-redirect")
        self.assertEqual(self.request("/", "HEAD").read(), b"")
