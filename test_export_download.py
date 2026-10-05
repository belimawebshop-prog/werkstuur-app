import ast
from datetime import date
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import TestCase, main
from urllib.parse import parse_qs, urlparse


class ExportDownloadTests(TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).parent / "cloud_server.py").read_text())
        handler = next(node for node in tree.body if getattr(node, "name", "") == "Handler")
        self.exports = []
        self.responses = []
        self.payload = {"meta": {"contains_passwords": False, "organization_id": 1}, "cases": []}
        def export():
            self.exports.append(1)
            return self.payload
        ns = {"BaseHTTPRequestHandler": BaseHTTPRequestHandler, "urlparse": urlparse,
              "parse_qs": parse_qs, "date": date, "export_payload": export,
              "_set_org_context": lambda value: None}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), "cloud_server.py", "exec"), ns)
        self.handler = object.__new__(ns["Handler"])
        self.handler._json = lambda payload, status=200, extra_headers=None: self.responses.append((status, payload, extra_headers))

    def request(self, path, role="admin"):
        self.handler.path = path
        def need(roles):
            if role is None:
                self.handler._json({"error": "unauthorized"}, 401)
                return None
            if role not in roles:
                self.handler._json({"error": "forbidden"}, 403)
                return None
            return {"id": 1, "role": role, "organization_id": 1}
        self.handler._need = need
        self.handler.do_GET()
        return self.responses[-1]

    def test_download_requires_login_before_reading_export(self):
        result = self.request("/api/export?download=1", role=None)
        self.assertEqual(result[0], 401)
        self.assertEqual(self.exports, [])

    def test_planner_and_technician_cannot_download_admin_export(self):
        for role in ("planner", "technician"):
            with self.subTest(role=role):
                result = self.request("/api/export?download=1", role=role)
                self.assertEqual(result[0], 403)
                self.assertEqual(self.exports, [])

    def test_download_and_existing_json_endpoint_return_the_same_export(self):
        status, data, headers = self.request("/api/export?download=1")
        self.assertEqual(status, 200)
        self.assertEqual(data, self.payload)
        self.assertTrue(headers["Content-Disposition"].startswith('attachment; filename="werkstuur-export-'))
        self.assertTrue(headers["Content-Disposition"].endswith('.json"'))
        status, regular, regular_headers = self.request("/api/export")
        self.assertEqual((status, regular, regular_headers), (200, self.payload, None))


if __name__ == "__main__":
    main(verbosity=2)
