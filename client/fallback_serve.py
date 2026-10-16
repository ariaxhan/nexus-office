"""Inert MacBook Office fallback: cached reads only, no Office runtime imports.

No service is installed by this module. A future MacBook operator must provide
an independently copied snapshot, a Tailscale identity, and phone readback
before this can be advertised as a fallback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import time

MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def read_snapshot(path: Path | None, max_age_s: int, now: float | None = None) -> dict:
    """Read one bounded, explicitly supplied snapshot. Never refresh it."""
    now = time.time() if now is None else now
    if path is None:
        return {"ok": False, "reason": "no snapshot configured", "world": None,
                "snapshot_at": None, "age_s": None}
    if path.is_symlink() or not path.is_file():
        return {"ok": False, "reason": "snapshot unavailable", "world": None,
                "snapshot_at": None, "age_s": None}
    try:
        if path.stat().st_size > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds 2 MiB")
        with path.open("rb") as handle:
            raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds 2 MiB")
        world = json.loads(raw)
        if not isinstance(world, dict) or not isinstance(world.get("stations"), list):
            raise ValueError("snapshot shape invalid")
        stamp = world.get("generated")
        if not isinstance(stamp, str):
            raise ValueError("snapshot has no generated time")
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("snapshot time has no timezone")
        age = now - parsed.timestamp()
        if age < -60:
            raise ValueError("snapshot time is future")
        return {"ok": age <= max_age_s, "reason": None if age <= max_age_s else "snapshot stale",
                "world": world, "snapshot_at": stamp, "age_s": round(age)}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"ok": False, "reason": "snapshot unreadable", "world": None,
                "snapshot_at": None, "age_s": None}


def make_server(port: int = 9443, snapshot: Path | None = None,
                trusted_host: str = "", login: str = "", max_age_s: int = 900):
    """Return an unstarted loopback server with only three GET routes."""
    if not 0 <= port <= 65535 or max_age_s < 1:
        raise ValueError("invalid fallback port or max age")
    trusted_host = trusted_host.strip().lower()
    login = login.strip().lower()
    if trusted_host and not login:
        raise ValueError("a trusted tailnet host requires a login")
    if login and not trusted_host:
        raise ValueError("a login requires a trusted tailnet host")
    local_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        timeout = 10
        def _send(self, code: int, body: bytes, content_type: str):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: dict, code: int = 200):
            self._send(code, json.dumps(data, separators=(",", ":")).encode(), "application/json")

        def _authorized(self) -> bool:
            host = self.headers.get("Host", "").lower()
            if trusted_host:
                return bool(host == trusted_host and
                            hmac.compare_digest(
                                self.headers.get("Tailscale-User-Login", "").lower().encode(),
                                login.encode()))
            if host in local_hosts:
                return True
            return False

        def do_GET(self):  # noqa: N802
            if not self._authorized():
                return self._json({"error": "forbidden"}, 403)
            route = self.path.partition("?")[0]
            if route not in ("/", "/api/health", "/api/world"):
                return self._json({"error": "not found"}, 404)
            state = read_snapshot(snapshot, max_age_s)
            if route == "/api/health":
                return self._json({"ok": state["ok"], "mode": "read-only-fallback",
                                   "snapshot_at": state["snapshot_at"],
                                   "age_s": state["age_s"], "reason": state["reason"]},
                                  200 if state["ok"] else 503)
            if route == "/api/world":
                return self._json({"mode": "read-only-fallback", "fresh": False,
                                   "at": state["snapshot_at"], "world": state["world"],
                                   "reason": state["reason"]},
                                  200 if state["ok"] else 503)
            status = ("Cached snapshot" if state["ok"] else
                      "Stale snapshot" if state["world"] is not None else
                      "Snapshot unavailable")
            cards = [(escape(str(row.get("repo", ""))),
                      escape(str(row.get("summary", "")))) for row in
                     (state["world"] or {}).get("stations", [])[:20]
                     if isinstance(row, dict) and row.get("repo")]
            items = "".join(f"<li><strong>{name}</strong><br>{summary}</li>"
                            for name, summary in cards)
            page = ("<!doctype html><html lang='en'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>Nexus Office · MacBook fallback</title>"
                    "<style>body{font-family:Georgia,serif;background:#f7f5f0;color:#242424;"
                    "max-width:720px;margin:8vh auto;padding:20px}h1{font-weight:400}"
                    "p,li{line-height:1.5}li{margin:0 0 12px}.note{border-top:1px solid #d5d1c9;padding-top:16px;"
                    "color:#515151}</style><main><p>NEXUS OFFICE · MACBOOK</p>"
                    f"<h1>{status}</h1><p>Read only. Last source snapshot: "
                    f"{escape(state['snapshot_at'] or 'none')}. "
                    f"Age: {state['age_s'] if state['age_s'] is not None else 'unknown'} seconds.</p>"
                    f"<ul>{items}</ul><p class='note'>This fallback cannot run work,"
                    "send messages, answer gates, or make changes. Open Studio to answer gates."
                    "</p></main></html>")
            self._send(200, page.encode(), "text/html; charset=utf-8")

        def _deny_write(self):
            self._json({"error": "read-only fallback"}, 405)

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _deny_write

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    local_hosts.update({f"127.0.0.1:{server.server_port}", f"localhost:{server.server_port}"})
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="cached, read-only MacBook Office fallback")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--trusted-host", default="")
    parser.add_argument("--login", default="")
    parser.add_argument("--max-age-s", type=int, default=900)
    args = parser.parse_args(argv)
    server = make_server(args.port, args.snapshot, args.trusted_host,
                         args.login, args.max_age_s)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
