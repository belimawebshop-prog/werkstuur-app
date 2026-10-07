"""Real HTTP handlers with synthetic roles and an in-memory database/storage.

No production login, Supabase connection or delivered email is involved.
"""
import ast
import base64
import copy
import hashlib
import hmac
import html
import http.cookiejar
import io
import json
import mimetypes
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlparse

import analysis_engine
import backup_archive
import operations
import support_access
import server as core

# Production has pinned httpx; the isolated fixture needs only its exception names.
class FixtureTransportError(Exception):
    pass
httpx=SimpleNamespace(**{name:FixtureTransportError for name in ("ReadError","ReadTimeout","ConnectError","ConnectTimeout")})

ROOT = Path(__file__).resolve().parent


class Query:
    def __init__(self, database, table):
        self.database, self.table = database, table
        self.filters, self.sorts = [], []
        self.columns, self.cap, self.operation, self.payload = "*", None, "select", None
        self.offset = 0

    def select(self, columns="*", **kwargs):
        self.columns = columns
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.sorts.append((field, desc))
        return self

    def limit(self, count):
        self.cap = count
        return self

    def range(self, start, end):
        self.offset, self.cap = start, end-start+1
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def execute(self):
        rows = self.database.rows.setdefault(self.table, [])
        selected = [row for row in rows if all(row.get(k) == v for k, v in self.filters)]
        if self.operation == "insert":
            added = self.payload if isinstance(self.payload, list) else [self.payload]
            selected = []
            for original in added:
                row = copy.deepcopy(original)
                row.setdefault("id", max([r.get("id", 0) for r in rows], default=0) + 1)
                if self.table == "cases":
                    row.setdefault("version", 1)
                rows.append(row)
                selected.append(row)
        elif self.operation == "update":
            for row in selected:
                row.update(copy.deepcopy(self.payload))
        elif self.operation == "delete":
            self.database.rows[self.table] = [row for row in rows if row not in selected]
        for field, descending in reversed(self.sorts):
            selected.sort(key=lambda row: (row.get(field) is not None, row.get(field) or ""), reverse=descending)
        if self.cap is not None:
            selected = selected[self.offset:self.offset+self.cap]
        selected = copy.deepcopy(selected)
        if self.columns != "*":
            fields = self.columns.split(",")
            selected = [{key: row.get(key) for key in fields} for row in selected]
        return SimpleNamespace(data=selected)


class MemoryDatabase:
    def __init__(self, rows):
        self.rows, self.files = copy.deepcopy(rows), {}
        self.storage = self

    def table(self, name):
        return Query(self, name)

    def from_(self, bucket):
        return self

    def download(self, path):
        return self.files[path]

    def upload(self, path, file, **kwargs):
        self.files[path] = bytes(file)
        return {"path": path}

    def remove(self, paths):
        for path in paths:
            self.files.pop(path, None)
        return []

    def rpc(self, name, params=None):
        """Auth database double; actual SQL functions are separately tested in Postgres."""
        params=params or {}
        def run():
            actor=next((u for u in self.rows["users"] if u["id"]==params["p_actor_id"] and u.get("active")),None)
            oid=params.get("p_organization_id")
            def grant(target):
                if not actor or not actor.get("is_platform_owner") or actor["organization_id"]==target:return None
                org=next((o for o in self.rows["organizations"] if o["id"]==target),{})
                if org.get("status") in ("suspended","archived"):return None
                for r in reversed(self.rows.get("support_access_requests",[])):
                    admin=next((u for u in self.rows["users"] if u["id"]==r.get("decided_by")),{})
                    if r["organization_id"]==target and r["requester_id"]==actor["id"] and support_access.effective_status(r)=="approved" and admin.get("active") and admin.get("role")=="admin" and not admin.get("is_platform_owner") and admin.get("organization_id")==target:
                        return {"id":r["id"],"organization_id":target,"scope":"workspace_read","expires_at":r["expires_at"],"approved_minutes":r["approved_minutes"],"approved_by":admin["display_name"]}
                return None
            if name=="werkstuur_support_access_check":return {"grant":grant(oid)}
            if name=="werkstuur_support_context":
                session=next((s for s in self.rows["sessions"] if s["token_hash"]==params["p_session_hash"] and s["user_id"]==actor["id"]),None) if actor else None
                if not actor or not actor.get("is_platform_owner") or not session:return {"error":"Geen eigenaarstoegang.","http_status":403}
                if oid!=actor["organization_id"] and not grant(oid):return {"error":"Eerst toestemming aanvragen.","http_status":403,"code":"support_access_required"}
                org=next((o for o in self.rows["organizations"] if o["id"]==oid),None)
                if not org:return {"error":"Organisatie niet gevonden.","http_status":404}
                session["active_organization_id"]=oid
                return {"ok":True,"organization":copy.deepcopy(org)}
            raise AssertionError("Unexpected RPC: "+name)
        return SimpleNamespace(execute=lambda:SimpleNamespace(data=run()))

    def approve_support_fixture(self, requester=1, org=2, admin=2, minutes=30):
        stamp=datetime.now(timezone.utc)
        rows=self.rows.setdefault("support_access_requests",[])
        row={"id":len(rows)+1,"organization_id":org,"requester_id":requester,"reason":"TEST alleen fictieve inzage",
             "requested_minutes":minutes,"approved_minutes":minutes,"status":"approved","version":2,
             "created_at":stamp.isoformat(),"request_expires_at":(stamp+timedelta(hours=24)).isoformat(),
             "decided_by":admin,"decided_at":stamp.isoformat(),"expires_at":(stamp+timedelta(minutes=minutes)).isoformat()}
        rows.append(row)
        return row


class RefinementWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(20)
        password_hash = core.hash_password(cls.password)
        actors = [(1, "admin", 1, True), (2, "admin", 2, False),
                  (3, "planner", 2, False), (4, "technician", 2, False),
                  (5, "technician", 2, False), (6, "planner", 3, False)]
        cls.seed = {"users": [{"id": uid, "role": role, "organization_id": org,
                             "is_platform_owner": owner, "active": True,
                             "display_name": f"Testrol {uid}", "email": f"actor{uid}@test.invalid",
                             "password_hash": password_hash} for uid, role, org, owner in actors],
                    "organizations": [{"id": i, "name": f"Testbedrijf {i}", "status": "active", "slug": f"test-{i}"} for i in range(1, 4)],
                    "organization_settings": [{"organization_id": 2, "key": "company_name", "value": "TEST Klantbedrijf"},
                                              {"organization_id": 2, "key": "intake_token", "value": "isolated-test-intake"}],
                    "cases": [], "notes": [], "attachments": [], "audit": [],
                    "sessions": [], "pilots": [], "pilot_snapshots": [], "settings": []}
        ns = {"__name__": "isolated_http_fixture", "base64": base64, "hashlib": hashlib,
              "hmac": hmac, "json": json, "mimetypes": mimetypes, "html_lib": html,
              "re": re, "secrets": secrets, "sys": sys, "threading": threading, "time": time,
              "Counter": Counter, "defaultdict": defaultdict, "datetime": datetime,
              "timezone": timezone, "timedelta": timedelta, "date": date, "Path": Path,
              "urlparse": urlparse, "parse_qs": parse_qs, "quote": quote,
              "BaseHTTPRequestHandler": BaseHTTPRequestHandler, "core": core,
              "analysis_engine": analysis_engine, "backup_archive": backup_archive,"operations":operations,"support_access":support_access,"httpx":httpx,"os":os,
              "APP_NAME": "Werkstuur", "APP_VERSION": "2.0.3-refined", "APP_BUILD": "2026-10-05",
              "ROOT": ROOT, "STATIC": ROOT, "BUCKET": "isolated-private-files", "MAX_BODY": 7 * 1024 * 1024,
              "SESSION_HOURS": 12, "COOKIE_SECURE": False, "PASSWORD_RESET_MINUTES": 30,
              "APP_PUBLIC_URL": "https://portal.werkstuur.nl", "WEBSITE_URL": "https://werkstuur.nl",
              "SMTP_REPLY_TO": "support@test.invalid", "SALES_EMAIL": "owner@test.invalid",
              "PUBLIC_SITE_ORIGINS": {"https://werkstuur.nl"}, "_org_local": threading.local(),
              "_RATE_LOCK": threading.Lock(), "_RATE_STATE": {}, "_RATE_SALT": secrets.token_bytes(16)}
        tree = ast.parse((ROOT / "cloud_server.py").read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) or getattr(node, "name", "") == "Handler" or (isinstance(node, ast.ImportFrom) and node.module == "__future__")]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "cloud_server.py", "exec"), ns)
        ns["_reset_secret"] = lambda: b"isolated-test-secret-not-a-production-key"
        ns["_mail_configured"] = lambda: True
        ns["_record_server_error"] = lambda scope, exc: cls.server_errors.append((scope, repr(exc)))
        ns["_queue_email"] = lambda *args, **kwargs: cls.mail.append((args, kwargs)) or True
        ns["_send_email"] = lambda *args, **kwargs: cls.mail.append((args, kwargs)) or {"ok": True}
        cls.ns = ns
        handler = ns["Handler"]
        handler.log_message = lambda self, *args: None
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.origin = f"http://127.0.0.1:{cls.http.server_port}"
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def setUp(self):
        self.db = MemoryDatabase(self.seed)
        self.ns["sb"] = self.db
        self.ns["_RATE_STATE"] = {}
        type(self).mail, type(self).server_errors = [], []

    def request(self, client, path, body=None, method=None, headers=None):
        request = urllib.request.Request(self.origin + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method or ("GET" if body is None else "POST"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = client.open(request)
        except urllib.error.HTTPError as exc:
            response = exc
        data = response.read()
        return response.status, json.loads(data) if "application/json" in response.headers.get("Content-Type", "") else data, response.headers

    def client(self, uid=None, password=None):
        jar = http.cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        if uid:
            status, result, headers = self.request(client, "/api/login", {"email": f"actor{uid}@test.invalid", "password": password or self.password})
            self.assertEqual(status, 200)
            self.assertNotIn("password_hash", result["user"])
            self.assertIn("HttpOnly", headers["Set-Cookie"])
            self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        return client

    def test_complete_customer_planner_technician_director_workflow(self):
        anonymous, director, planner, technician = self.client(), self.client(2), self.client(3), self.client(4)
        status, created, _ = self.request(anonymous, "/api/public-intake", {
            "token": "isolated-test-intake", "privacy_acknowledged": True,
            "customer": "TEST — geen echte klant", "city": "Testomgeving", "email": "customer@test.invalid",
            "type": "Laadpaal", "problem": "Easee offline; geen rook of brandlucht.",
            "extra": {"manufacturer": "Easee", "app_online": "offline"}})
        self.assertEqual(status, 201)
        cid = self.db.rows["cases"][0]["id"]
        self.assertEqual(created["case_no"], self.db.rows["cases"][0]["case_no"])
        # Customer confirmation and planner notifications are rendered, never sent.
        self.assertGreaterEqual(len(self.mail), 2)
        self.assertTrue(all("werkstuur" in str(args).casefold() for args, _ in self.mail))
        self.assertEqual(self.request(technician, "/api/cases")[1], [])
        case = self.request(planner, "/api/cases")[1][0]
        self.assertEqual(case["organization_id"], 2)
        self.assertIn("analysis", case)
        status, case, _ = self.request(planner, f"/api/cases/{cid}", {"version": case["version"], "assigned_to": 4, "status": "Ingepland"}, "PATCH")
        self.assertEqual(status, 200)
        self.assertEqual(self.request(technician, "/api/cases")[1][0]["id"], cid)
        self.assertEqual(self.request(self.client(5), f"/api/cases/{cid}/notes")[0], 404)
        self.assertEqual(self.request(self.client(6), f"/api/cases/{cid}/notes")[0], 404)
        self.assertEqual(self.request(technician, f"/api/cases/{cid}/notes", {"body": "Fictieve overdrachtsnotitie"})[0], 201)
        raw = b"synthetic-private-photo-bytes"
        status, attachment, _ = self.request(technician, f"/api/cases/{cid}/attachments", {
            "filename": "testfoto.png", "content_type": "image/png", "data_base64": base64.b64encode(raw).decode()})
        self.assertEqual(status, 201)
        self.assertEqual(self.request(planner, f"/api/attachments/{attachment['id']}")[1], raw)
        self.assertEqual(self.request(anonymous, f"/api/attachments/{attachment['id']}")[0], 401)
        status, outcome, _ = self.request(technician, f"/api/cases/{cid}/outcome", {"remote_resolved": True, "notes": "Uitsluitend geïsoleerde test", "planner_minutes": 4})
        self.assertEqual(status, 200)
        status, closed, _ = self.request(technician, f"/api/cases/{cid}", {"version": case["version"], "status": "Afgerond"}, "PATCH")
        self.assertEqual(status, 200)
        self.assertTrue(closed["closed_at"])
        self.assertTrue(self.request(director, "/api/cases")[1][0]["outcome_remote_resolved"])
        status, zipped, headers = self.request(director, "/api/export?format=zip")
        self.assertEqual(status, 200)
        self.assertIn("application/zip", headers["Content-Type"])
        payload, manifest, files = backup_archive.verify_archive(zipped)
        self.assertEqual(payload["cases"][0]["status"], "Afgerond")
        self.assertIn(raw, files.values())
        with tempfile.TemporaryDirectory() as temporary:
            restored = Path(temporary) / "recovery"
            result = backup_archive.restore_to_new_folder(zipped, restored)
            self.assertEqual(result["attachments"], 1)
            self.assertEqual(json.loads((restored / "backup.json").read_text()), payload)
        self.assertEqual(self.server_errors, [])

    def test_complete_password_recovery_invalidates_old_sessions_and_link(self):
        old_session = self.client(3)
        old_hash = self.db.rows["users"][2]["password_hash"]
        token = self.ns["create_password_reset_token"](self.db.rows["users"][2])
        self.assertEqual(self.request(self.client(), "/api/forgot-password", {"email": "actor3@test.invalid"})[0], 200)
        self.assertEqual(len(self.mail), 1)
        args, kwargs = self.mail[0]
        self.assertEqual(args[0], "actor3@test.invalid")
        self.assertIn("https://portal.werkstuur.nl/?reset=", args[2])
        self.assertNotIn(old_hash, str(args))
        new_password = secrets.token_urlsafe(22)
        self.assertEqual(self.request(self.client(), "/api/reset-password", {"token": token, "new_password": new_password})[0], 200)
        self.assertEqual(self.request(old_session, "/api/me")[0], 401)
        self.assertEqual(self.request(self.client(), "/api/login", {"email": "actor3@test.invalid", "password": self.password})[0], 401)
        self.assertEqual(self.request(self.client(3, new_password), "/api/me")[1]["role"], "planner")
        self.assertEqual(self.request(self.client(), "/api/reset-password", {"token": token, "new_password": new_password})[0], 400)
        self.assertEqual(self.server_errors, [])

    def test_unknown_email_gets_same_recovery_response(self):
        known = self.request(self.client(), "/api/forgot-password", {"email": "actor3@test.invalid"})
        unknown = self.request(self.client(), "/api/forgot-password", {"email": "absent@test.invalid"})
        self.assertEqual(known[:2], unknown[:2])
        self.assertEqual(len(self.mail), 1)

    def test_expired_and_tampered_recovery_links_are_rejected(self):
        row = self.db.rows["users"][2]
        token = self.ns["create_password_reset_token"](row)
        original_time = self.ns["time"]
        self.ns["time"] = SimpleNamespace(time=lambda: original_time.time() + 3600)
        try:
            self.assertIsNone(self.ns["verify_password_reset_token"](token))
        finally:
            self.ns["time"] = original_time
        self.assertIsNone(self.ns["verify_password_reset_token"](token + "changed"))

    def test_recovery_rejects_short_password_without_invalidating_link(self):
        token = self.ns["create_password_reset_token"](self.db.rows["users"][2])
        self.assertEqual(self.request(self.client(), "/api/reset-password", {"token": token, "new_password": "short"})[0], 400)
        self.assertIsNotNone(self.ns["verify_password_reset_token"](token))

    def test_complete_backups_are_admin_only_and_organization_scoped(self):
        for uid, expected in ((None, 401), (3, 403), (4, 403)):
            self.assertEqual(self.request(self.client(uid), "/api/export?format=zip")[0], expected)
        status, archive, _ = self.request(self.client(2), "/api/export?format=zip")
        self.assertEqual(status, 200)
        payload, manifest, _ = backup_archive.verify_archive(archive)
        self.assertEqual(manifest["organization_id"], 2)
        self.assertTrue(all(user["id"] in (2, 3, 4, 5) for user in payload["users"]))
        self.assertFalse(any("password_hash" in user for user in payload["users"]))

    def test_private_source_and_backup_files_are_not_public_static_assets(self):
        client = self.client()
        for path in ("/cloud_server.py", "/backup_archive.py", "/README.md", "/render.yaml", "/WERKSTUUR-HTTPS-MAIL-DEPLOYMENT.zip", "/.git/config", "/app%205.js"):
            self.assertEqual(self.request(client, path)[0], 404, path)
        for path in ("/", "/intake.html", "/app.js", "/refinements-v203.js", "/refinements-v203.css", "/icon-192.png"):
            self.assertEqual(self.request(client, path)[0], 200, path)

    def test_monteur_cannot_override_analysis_or_assignment_through_patch(self):
        planner, technician = self.client(3), self.client(4)
        status, case, _ = self.request(planner, "/api/cases", {"customer": "Fictief", "type": "Laadpaal", "problem": "Easee offline", "extra": {"manufacturer": "Easee"}})
        self.assertEqual(status, 201)
        status, assigned, _ = self.request(planner, f"/api/cases/{case['id']}", {"version": case["version"], "assigned_to": 4}, "PATCH")
        self.assertEqual(status, 200)
        before = copy.deepcopy(self.db.rows["cases"])
        for change in ({"assigned_to": 5}, {"score": 100}, {"dispatch": "Onterecht advies"}, {"problem": "Onterecht gewijzigde klacht"}):
            self.assertEqual(self.request(technician, f"/api/cases/{case['id']}", {"version": assigned["version"], **change}, "PATCH")[0], 403)
        self.assertEqual(self.db.rows["cases"], before)

    def test_malformed_status_and_version_are_client_errors_without_changes(self):
        planner = self.client(3)
        status, case, _ = self.request(planner, "/api/cases", {"customer": "Fictief", "type": "Laadpaal", "problem": "Easee offline", "extra": {"manufacturer": "Easee"}})
        self.assertEqual(status, 201)
        before = copy.deepcopy(self.db.rows["cases"])
        for body in ({"version": case["version"], "status": "invalid"}, {"version": "invalid", "status": "Review"}, {"version": True, "status": "Review"}):
            self.assertEqual(self.request(planner, f"/api/cases/{case['id']}", body, "PATCH")[0], 400)
        self.assertEqual(self.db.rows["cases"], before)
        self.assertEqual(self.server_errors, [])

    def test_public_contact_renders_safe_branded_email_without_delivery(self):
        client = self.client()
        self.assertEqual(self.request(client, "/api/public-contact", {"name": "TEST <script>", "email": "self@test.invalid", "company": "Testbedrijf", "message": "Fictieve kennismakingsaanvraag voor een geïsoleerde test."})[0], 200)
        args, kwargs = self.mail[0]
        self.assertEqual(args[0], "owner@test.invalid")
        self.assertEqual(kwargs["reply_to"], "self@test.invalid")
        self.assertNotIn("<script>", args[3])
        self.assertIn("&lt;script&gt;", args[3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
