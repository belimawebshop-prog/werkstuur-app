"""Retire the old app entry point without touching shared customer data.

The process uses no database or mail credentials. Old write requests stop here;
only safe browser navigation is forwarded to the current portal.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORTAL = "https://portal.werkstuur.nl"


class RedirectHandler(BaseHTTPRequestHandler):
    server_version = "WerkstuurRedirect/2.1.2"

    def log_message(self, fmt, *args):
        # Do not write intake or password recovery query parameters to logs.
        pass

    def reply(self, status, body, **headers):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path)
        if path.path == "/api/health":
            return self.reply(200, {"ok": True, "version": "2.1.2-redirect", "portal": PORTAL})
        if path.path.startswith("/api/"):
            return self.retired()
        # Preserve valid intake and recovery links. Never accept a supplied host.
        target = PORTAL + ("/intake.html" if path.path == "/intake.html" else "/")
        if path.path in ("/", "/index.html", "/intake.html") and path.query:
            target += "?" + path.query
        return self.reply(302, {"portal": PORTAL, "message": "Open de actuele Werkstuur-omgeving."}, Location=target)

    do_HEAD = do_GET

    def retired(self):
        return self.reply(410, {"error": "Deze oude omgeving is vervangen. Open portal.werkstuur.nl en meld je daar aan.", "portal": PORTAL}, Connection="close")

    do_POST = retired
    do_PATCH = retired
    do_PUT = retired
    do_DELETE = retired
    do_OPTIONS = retired


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), RedirectHandler).serve_forever()
