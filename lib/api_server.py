#!/usr/bin/env python3
"""MONITOR WIBU v2 — penerima laporan node (HTTP, stdlib saja).

Jalankan:  python3 api_server.py        (systemd: monitorwibu-api.service)
Env:
    MONITORWIBU_CONF   berisi BOT_TOKEN, CHAT_ID, MASTER_NAME, NODES_DB, STATE_DIR,
                PORT, BIND, TLS_CERT, TLS_KEY  (default /etc/monitorwibu/master.conf)
Keamanan:
    - kunci per node (tidak ada kunci "satu untuk semua"), tidak pernah dikirim ke jaringan
    - HMAC-SHA256 atas badan permintaan + jendela waktu 90 detik (anti-ulang)
    - pembatas laju per IP dan per node, batas ukuran badan, nama & field divalidasi
    - TLS opsional (TLS_CERT/TLS_KEY di master.conf)
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import monitorwibu  # noqa: E402

CONF_PATH = os.environ.get("MONITORWIBU_CONF", "/etc/monitorwibu/master.conf")
STATE_DIR = os.environ.get("MONITORWIBU_STATE", "/var/lib/monitorwibu")


def load_conf(path: str) -> dict:
    conf: dict[str, str] = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"')
    return conf


CONF = load_conf(CONF_PATH)
NODES_DB = CONF.get("NODES_DB", "/etc/monitorwibu/nodes.json")
REPORTS_DIR = os.path.join(CONF.get("STATE_DIR", STATE_DIR), "nodes")
PORT = int(CONF.get("PORT", "5000"))
BIND = CONF.get("BIND", "0.0.0.0")

RATE_IP = monitorwibu.RateLimiter(60, 60)
RATE_NODE = monitorwibu.RateLimiter(30, 60)
_db = monitorwibu.NodeDB(NODES_DB)


def db_changed() -> bool:
    """Muat ulang daftar node bila berkasnya berubah (add-node tanpa restart)."""
    global _db
    try:
        if os.path.getmtime(NODES_DB) != getattr(db_changed, "mtime", None):
            db_changed.mtime = os.path.getmtime(NODES_DB)
            _db = monitorwibu.NodeDB(NODES_DB)
            return True
    except OSError:
        pass
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "monitorwibu/2.0"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- utilitas
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[api] {self.address_string()} {fmt % args}", flush=True)

    # -------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            n = len(monitorwibu.read_reports(REPORTS_DIR, fresh=10 ** 9))
            self._send(200, json.dumps({"ok": True, "node": n, "time": int(time.time())}),
                       "application/json")
        else:
            self._send(404, "not found")

    # ------------------------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/api/report":
            self._send(404, "not found")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, "bad length")
            return
        if length <= 0 or length > monitorwibu.MAX_BODY:
            self._send(413, "too large")
            return

        if not RATE_IP.allow(self.client_address[0]):
            self._send(429, "slow down")
            return

        body = self.rfile.read(length)
        name = self.headers.get("X-Wibu-Node", "")
        ts = self.headers.get("X-Wibu-Ts", "0")
        sig = self.headers.get("X-Wibu-Sig", "")

        try:
            name = monitorwibu.sanitize_name(name)
        except monitorwibu.Invalid:
            self._send(400, "bad name")
            return
        if not RATE_NODE.allow(name):
            self._send(429, "slow down")
            return

        db_changed()
        key = _db.get(name)
        if not key:
            self._send(403, "unknown node")
            return

        ok, why = monitorwibu.verify(key, body, sig, ts)
        if not ok:
            self.log_message("tolak node=%s alasan=%s ip=%s", name, why, self.client_address[0])
            self._send(403, "bad signature")
            return

        try:
            payload = json.loads(body.decode())
            data = monitorwibu.parse_data(payload.get("data", {}))
        except (ValueError, monitorwibu.Invalid, UnicodeDecodeError):
            self._send(400, "bad payload")
            return

        try:
            monitorwibu.store_report(REPORTS_DIR, name, data, int(ts))
        except OSError as exc:
            self.log_message("gagal simpan %s: %s", name, exc)
            self._send(500, "store failed")
            return

        self._send(200, "OK")


def main() -> int:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    cert, keyfile = CONF.get("TLS_CERT"), CONF.get("TLS_KEY")
    if cert and keyfile and os.path.exists(cert) and os.path.exists(keyfile):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"
    print(f"[api] mendengarkan {scheme}://{BIND}:{PORT} · node terdaftar: {len(_db.nodes)}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
