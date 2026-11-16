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

import hashlib
import json
import os
import socket
import ssl
import sys
import threading
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
# E2-A1: pembatas pra-auth kasar (hujan sampah tetap dibatasi) — kuota per-IP node sah
# (RATE_IP) baru dikonsumsi SETELAH verifikasi tanda tangan sukses, jadi 60 kiriman
# sampah se-IP tidak bisa menghabiskan kuota node sah di belakang NAT yang sama.
RATE_PRE = monitorwibu.RateLimiter(600, 60)
RATE_NODE = monitorwibu.RateLimiter(30, 60)
# R4-B2: /health punya pembatas SENDIRI supaya namespace-nya tidak bisa memenuhi tabel
# ember RATE_IP (MAX_BUCKETS) sehingga laporan node sah dari IP baru ditolak 429.
RATE_HEALTH = monitorwibu.RateLimiter(60, 60)
_db = monitorwibu.NodeDB(NODES_DB)




def db_changed() -> None:
    """Muat ulang daftar node bila berkasnya berubah (add-node tanpa restart).

    Melempar DBDamaged bila nodes.json ada tapi rusak — _db lama TIDAK diganti,
    dan pemanggil (do_POST / do_GET /health) membalas HTTP, bukan memutus koneksi.
    """
    global _db
    # R5-F3: pra-saring murah — stat dulu; baca+hash hanya bila (mtime_ns, size)
    # berubah atau digest belum pernah dihitung. Jangan baca seluruh berkas tiap
    # permintaan (dulu ~0,93 ms pada nodes.json 1,16 MB, tumbuh linier).
    try:
        st = os.stat(NODES_DB)
    except OSError:
        return
    stat_sig = (st.st_mtime_ns, st.st_size)
    if stat_sig == getattr(db_changed, "stat_sig", None) \
            and getattr(db_changed, "digest", None) is not None:
        return
    # R4-C2b: deteksi perubahan dari ISI (hash), bukan mtime saja.
    try:
        with open(NODES_DB, "rb") as f:
            raw = f.read()
    except OSError:
        return
    digest = hashlib.sha256(raw).digest()
    if digest == getattr(db_changed, "digest", None):
        db_changed.stat_sig = stat_sig
        return
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("nodes.json bukan objek")
    except (ValueError, UnicodeDecodeError) as exc:
        db_changed.digest = None      # jangan anggap versi lama masih sah; coba ulang
        db_changed.stat_sig = None    # berkas dipulihkan tetap terdeteksi
        raise DBDamaged(str(exc))
    # penanda hanya diset setelah json valid -> berkas rusak tetap dicoba ulang
    db_changed.digest = digest
    db_changed.stat_sig = stat_sig
    db_changed.mtime = st.st_mtime
    _db = monitorwibu.NodeDB(NODES_DB)


class DBDamaged(Exception):
    """nodes.json rusak saat berjalan."""


class Handler(BaseHTTPRequestHandler):
    server_version = "monitorwibu/2.0"
    protocol_version = "HTTP/1.1"
    timeout = 10                    # B-2: jangan digantung slowloris
    # R5-F1: anggaran waktu TOTAL per siklus permintaan — mencakup pembacaan
    # request-line + header, bukan hanya badan. `timeout` hanya batas JEDA antar-recv,
    # jadi klien yang meneteskan 1 byte header tiap <timeout> detik menahan thread
    # selamanya (ThreadingHTTPServer tak membatasi jumlah thread).
    request_budget = 10

    # ------------------------------------------------------------- anggaran waktu
    def handle_one_request(self) -> None:
        """Jalankan satu siklus permintaan di bawah anggaran waktu total.

        Jam pengawas membatalkan (shutdown sisi baca) bila request-line/header/badan
        tak selesai dalam `request_budget` detik; pembacaan yang terblokir langsung
        kembali EOF sehingga stdlib menutup koneksi. Anggaran baru tiap permintaan
        (keep-alive tetap dilayani normal karena jam dibatalkan saat siklus selesai).
        """
        jam = threading.Timer(self.request_budget, self._anggaran_habis)
        jam.daemon = True
        jam.start()
        try:
            super().handle_one_request()
        finally:
            jam.cancel()

    def _anggaran_habis(self) -> None:
        self.close_connection = True
        try:
            self.connection.shutdown(socket.SHUT_RD)   # bangunkan recv yang terblokir
        except OSError:
            pass

    # ---------------------------------------------------------------- utilitas
    def _send(self, code: int, body: str, ctype: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        if self.close_connection:   # B-3: kabari klien bahwa koneksi ditutup
            self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[api] {self.address_string()} {fmt % args}", flush=True)

    # -------------------------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            # R4-B2/B-5: batasi laju /health (kunci bercabang "health:") tanpa memenuhi tabel
            # MAX_BUCKETS milik laporan node sah; laporan tetap dilayani walau tabel penuh
            # oleh kunci namespace /health (RATE_HEALTH terpisah).
            if not RATE_HEALTH.allow(self.client_address[0]):   # B-5: batasi laju
                self._send(429, "slow down")
                return
            # R5-F2: segarkan status registry di sini, jangan menunggu POST. Tanpa ini
            # /health bisa mengaku "ok" saat nodes.json rusak (dan POST akan 503), atau
            # tetap "damaged" setelah berkas dipulihkan sampai ada POST bernama sah.
            rusak_sebelum = getattr(_db, "damaged", False)
            digest_diketahui = getattr(db_changed, "digest", None) is not None
            try:
                db_changed()
            except DBDamaged:
                _db.damaged = True
            # R4-4: registry yang rusak tetapi BUKAN ditandai db_changed sendiri (mis.
            # NodeDB dibuat dari berkas rusak saat start) tidak boleh dipulihkan senyap
            # oleh /health — cukup lapor rusak sampai berkas benar-benar berubah.
            rusak = getattr(_db, "damaged", False) or (rusak_sebelum and digest_diketahui)
            reg = "damaged" if rusak else "ok"
            n = len(monitorwibu.read_reports(REPORTS_DIR, fresh=300))
            self._send(200, json.dumps({"ok": reg == "ok", "node": n, "registry": reg,
                                        "time": int(time.time())}),
                       "application/json")
        else:
            self.close_connection = True   # F8: jangan tinggalkan badan (cegah desync)
            self._send(404, "not found")

    # ------------------------------------------------------------------- POST
    def _read_body(self, length: int) -> bytes | None:
        """Baca tepat `length` byte dengan DEADLINE TOTAL (R4-A1).

        self.timeout hanya membatasi jeda ANTAR-recv; tanpa batas total, klien tanpa
        autentikasi bisa meneteskan 1 byte tiap <timeout> detik dan menahan thread
        (ThreadingHTTPServer tak membatasi jumlah thread). Kembalikan None bila
        deadline terlampaui / klien menutup lebih awal -> pemanggil membalas 408.
        """
        deadline = time.monotonic() + (self.timeout or 10)
        buf = bytearray()
        try:
            while len(buf) < length:
                sisa = deadline - time.monotonic()
                if sisa <= 0:
                    return None
                self.connection.settimeout(sisa)     # batas antar-recv = sisa deadline
                try:
                    # read1 (bukan read): read() menelan socket timeout, read1 meneruskannya
                    chunk = self.rfile.read1(length - len(buf))
                except TimeoutError:
                    return None                      # deadline total terlampaui
                if not chunk:                        # klien tutup sebelum badan lengkap
                    return None
                buf += chunk
            return bytes(buf)
        finally:
            self.connection.settimeout(self.timeout)  # pulihkan untuk keep-alive

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/api/report":
            # R4-1: badan tak dibaca -> tutup koneksi (cegah desync/request smuggling)
            self.close_connection = True
            self._send(404, "not found")
            return

        # R4-A4: Content-Length ganda / Transfer-Encoding -> 400 + tutup koneksi
        # (cegah request smuggling: sisa badan jangan diurai sebagai request berikutnya)
        cls = self.headers.get_all("Content-Length") or []
        if len(cls) > 1 or self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            self._send(400, "bad length")
            return
        try:
            length = int(cls[0]) if cls else 0
        except ValueError:
            self.close_connection = True
            self._send(400, "bad length")
            return
        if length <= 0 or length > monitorwibu.MAX_BODY:
            # B-3: badan tidak dibaca -> tutup koneksi (cegah desync/smuggling)
            self.close_connection = True
            self._send(413, "too large")
            return

        # E2-A1/R4-2: RATE_PRE membatasi SAMPAH pra-auth. Badan SELALU dibaca lebih dulu
        # (buang sisa -> tak ada desync/501); permintaan yang lolos verifikasi tanda tangan
        # tidak dibungkam kuota pra-auth (kuota node sah = RATE_IP/RATE_NODE).
        pre_denied = not RATE_PRE.allow(self.client_address[0])

        # R4-A1: baca badan dengan DEADLINE TOTAL (self.timeout hanya batas antar-recv,
        # tanpa ini klien tanpa autentikasi bisa meneteskan 1 byte tiap <timeout> detik
        # dan menahan thread; ThreadingHTTPServer tak membatasi jumlah thread).
        body = self._read_body(length)
        if body is None:
            self.close_connection = True
            self._send(408, "request timeout")
            return
        name = self.headers.get("X-Wibu-Node", "")
        ts = self.headers.get("X-Wibu-Ts", "0")
        sig = self.headers.get("X-Wibu-Sig", "")

        try:
            name = monitorwibu.sanitize_name(name)
        except monitorwibu.Invalid:
            if pre_denied:
                self._send(429, "slow down")
            else:
                self._send(400, "bad name")
            return

        # B-4/C-6: daftar kunci rusak -> balas galat, jangan putus koneksi
        try:
            db_changed()
        except DBDamaged as exc:
            _db.damaged = True          # R4-C2: tandai registry rusak agar /health tak menghijau
            self.log_message("tolak node=%s alasan=registry-rusak ip=%s (%s)",
                             name, self.client_address[0], exc)
            self._send(503, "registry unavailable")
            return

        # B-1: verifikasi tanda tangan DULU, baru konsumsi batas laju per node
        key = _db.get(name)
        if not key:
            self.log_message("tolak node=%s alasan=unknown node ip=%s",
                             name, self.client_address[0])
            # B-13: seragam; pra-auth lewat kuota -> seragam 429 juga
            if pre_denied:
                self._send(429, "slow down")
            else:
                self._send(403, "forbidden")
            return

        ok, why = monitorwibu.verify(key, body, sig, ts)
        if not ok:
            reason = {"sig": "bad signature", "sig-format": "bad signature",
                      "ts-window": "ts-window", "ts": "ts-window"}.get(why, why)
            self.log_message("tolak node=%s alasan=%s ip=%s", name, reason, self.client_address[0])
            if pre_denied:
                self._send(429, "slow down")
            else:
                self._send(403, "forbidden")
            return

        # E2-A1: kuota per-IP dikonsumsi hanya oleh request yang LOLOS verifikasi.
        # laporan sah dari IP baru tetap 200 walau tabel dipenuhi kunci /health.
        if not RATE_IP.allow(self.client_address[0]):
            self._send(429, "slow down")
            return

        if not RATE_NODE.allow(name):
            self._send(429, "slow down")
            return

        try:
            payload = json.loads(body.decode())
            data = monitorwibu.parse_data(payload.get("data", {}))
        except (ValueError, monitorwibu.Invalid, UnicodeDecodeError, AttributeError, TypeError):
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
