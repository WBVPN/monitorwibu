#!/usr/bin/env python3
"""MONITOR WIBU v2 — inti logika bersama master & node.

Tanpa dependensi luar (stdlib saja) supaya bisa diuji langsung:
    python3 tests/test_monitorwibu.py

Protokol (bukan X-API-Key lagi):
    POST /api/report      body: JSON
    X-Wibu-Node : nama node
    X-Wibu-Ts   : epoch detik
    X-Wibu-Sig   : HMAC-SHA256(body, kunci node) hex

Kunci tidak pernah dikirim; master menyimpan kunci per node, jadi satu node bocor
tidak bisa meniru node lain. Tanda tangan berlaku 90 detik (anti-ulang).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import sys
import tempfile
import time

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
SERVICES_RE = re.compile(r"[^:]{1,40}:(up|down)")
TS_SKEW = 90            # detik, jendela anti-ulang
MAX_BODY = 96 * 1024    # batas badan permintaan
MAX_FIELDS = 40
MAX_VALUE = 400
FRESH = 300             # laporan lebih tua dari ini dianggap basi


class Invalid(Exception):
    """Masukan tidak sah (dipakai untuk balasan 400 tanpa membocorkan alasan rinci)."""


# ---------------------------------------------------------------- nama & data
def sanitize_name(name: str) -> str:
    """Bersihkan nama node; hasil kosong/terlalu panjang = Invalid."""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise Invalid("nama node hanya boleh A-Z a-z 0-9 _ - (1-50 karakter)")
    return name


def parse_data(data) -> dict:
    """Validasi payload metrik: dict sederhana, jumlah & panjang terbatas."""
    if not isinstance(data, dict):
        raise Invalid("data harus objek JSON")
    if len(data) > MAX_FIELDS:
        raise Invalid("terlalu banyak field")
    out = {}
    for k, v in data.items():
        if not isinstance(k, str) or not k or len(k) > 40:
            raise Invalid("nama field tidak sah")
        if k == "services":
            # WAJIB daftar "nama:up|down"; satu node ngawur tidak boleh menjatuhkan render.
            if not (isinstance(v, list) and len(v) <= 20
                    and all(isinstance(x, str) and SERVICES_RE.fullmatch(x) for x in v)):
                raise Invalid("daftar layanan tidak sah")
            out[k] = v
            continue
        if isinstance(v, bool) or isinstance(v, (int, float)):
            out[k] = v
        elif isinstance(v, str):
            if len(v) > MAX_VALUE or any(ord(c) < 32 and c not in "\t" for c in v):
                raise Invalid("nilai field tidak sah")
            out[k] = v
        elif isinstance(v, list):
            if len(v) > 20 or any(not isinstance(x, str) or len(x) > 100 for x in v):
                raise Invalid("daftar tidak sah")
            out[k] = v
        else:
            raise Invalid("tipe field tidak sah")
    return out


# ------------------------------------------------------------------ tanda tangan
def sign(key: str, body: bytes) -> str:
    return hmac.new(key.encode(), body, hashlib.sha256).hexdigest()


def verify(key: str, body: bytes, sig: str, ts: int, now: int | None = None,
           skew: int = TS_SKEW) -> tuple[bool, str]:
    """Periksa tanda tangan + jendela waktu. Kembalikan (ok, alasan)."""
    now = int(time.time()) if now is None else int(now)
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False, "ts"
    if abs(now - ts) > skew:
        return False, "ts-window"
    if not sig or not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False, "sig-format"
    if not hmac.compare_digest(sign(key, body), sig):
        return False, "sig"
    return True, "ok"


# ------------------------------------------------------------------ basis kunci
class NodeDB:
    """Daftar node + kunci (JSON, mode 600)."""

    def __init__(self, path: str):
        self.path = path
        self.nodes: dict[str, str] = {}
        self.damaged = False          # R4-4: registry tak terpakai (rusak / bukan objek)
        self.load()

    def load(self) -> None:
        """Jangan pernah melempar: nodes.json rusak/bukan objek -> dict kosong + peringatan."""
        self.damaged = False
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("isi bukan objek JSON")
            self.nodes = data
        except Exception as exc:      # E2-E/R4-4: jangan kehilangan layanan senyap
            self.nodes = {}
            self.damaged = True
            print(f"[db] nodes.json rusak: {exc}", file=sys.stderr, flush=True)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path))
        with os.fdopen(fd, "w") as f:
            json.dump(self.nodes, f, indent=1, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def get(self, name: str) -> str | None:
        return self.nodes.get(name)

    def add(self, name: str) -> str:
        name = sanitize_name(name)
        key = os.urandom(32).hex()
        self.nodes[name] = key
        self.save()
        return key

    def remove(self, name: str) -> bool:
        if name in self.nodes:
            del self.nodes[name]
            self.save()
            return True
        return False


# ------------------------------------------------------------------- penyimpanan
def store_report(dirpath: str, name: str, data: dict, ts: int) -> None:
    """Tulis laporan node secara atomik; satu berkas per node."""
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirpath)
    with os.fdopen(fd, "w") as f:
        json.dump({"ts": int(ts), "data": data}, f)
    os.replace(tmp, os.path.join(dirpath, f"{sanitize_name(name)}.json"))


def read_reports(dirpath: str, fresh: int = FRESH) -> list[tuple[str, dict, int]]:
    """Baca laporan segar -> [(nama, data, umur_detik)] berurut nama."""
    out = []
    now = time.time()
    for fn in sorted(os.listdir(dirpath)) if os.path.isdir(dirpath) else []:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(dirpath, fn)
        try:
            with open(path) as f:
                obj = json.load(f)
            age = now - float(obj.get("ts", 0))
        except Exception:
            continue
        if age <= fresh:            # float, agar sepakat dgn prune (age > fresh)
            data = obj.get("data", {})
            # E2-F2: data bukan objek -> {} supaya satu berkas rusak tak mematikan render
            out.append((fn[:-5], data if isinstance(data, dict) else {}, int(age)))
    return out


def prune_reports(dirpath: str, fresh: int = FRESH) -> int:
    """Hapus laporan basi -> jumlah berkas yang dibuang."""
    n = 0
    if not os.path.isdir(dirpath):
        return 0
    for fn in os.listdir(dirpath):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(dirpath, fn)
        try:
            with open(path) as f:
                ts = float(json.load(f).get("ts", 0))
        except Exception:
            ts = 0
        if time.time() - ts > fresh:
            os.unlink(path)
            n += 1
    return n


# ------------------------------------------------------------------ pembatasan
class RateLimiter:
    """Pembatas laju sederhana dalam memori (per kunci, jendela geser kasar).

    Jumlah ember dibatasi (MAX_BUCKETS) supaya kunci dari header penyerang
    tidak menumbuhkan memori tanpa batas.
    """

    MAX_BUCKETS = 4096

    def __init__(self, limit: int, window: int = 60):
        self.limit = limit
        self.window = window
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        baru = key not in self.hits
        lst = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(lst) >= self.limit:
            self.hits[key] = lst
            return False
        if baru and len(self.hits) >= self.MAX_BUCKETS:
            # E2-D: ember penuh -> jangan buang ember aktif; tolak kunci baru saja
            self._evict(now)
            if len(self.hits) >= self.MAX_BUCKETS:
                return False
        lst.append(now)
        self.hits[key] = lst
        return True

    def _evict(self, now: float) -> None:
        """Buang HANYA ember yang benar-benar kedaluwarsa (jangan reset ember aktif)."""
        for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= self.window]:
            del self.hits[k]


# ---------------------------------------------------------------------- render
def _esc(v) -> str:
    return html.escape(str(v), quote=False)


def _badge(active: bool) -> str:
    return "🟢 <b>ACTIVE</b>" if active else "🔴 <b>CRITICAL</b>"


def render_block(name: str, data: dict) -> str:
    """Blok satu node untuk pesan Telegram (semua nilai di-escape)."""
    if not isinstance(data, dict):   # E2-F2: tahan data non-objek
        data = {}
    warn = []
    try:
        if float(data.get("ram_pct", 0)) >= 90:
            warn.append("RAM tinggi")
        if float(data.get("disk_pct", 0)) >= 90:
            warn.append("Disk hampir penuh")
    except (TypeError, ValueError):
        pass
    svcs = data.get("services")
    if not isinstance(svcs, list):     # data ngawur: jangan meledak
        svcs = []
    down = [str(s).split(":")[0] for s in svcs if str(s).endswith(":down")]
    if down:
        warn.append("Layanan mati: " + ", ".join(_esc(d) for d in down))

    lines = [
        f"👑 <b>SERVER : {_esc(name.upper())}</b>",
        f" ┣ 🌐 <b>Domain :</b> <code>{_esc(data.get('domain', '-'))}</code>",
        f" ┣ 🔌 <b>IPv4   :</b> <code>{_esc(data.get('ip', '-'))}</code>",
        f" ┣ 🏙️ <b>Lokasi :</b> {_esc(data.get('city', 'Unknown'))} (Auto)",
        f" ┣ ⏳ <b>Uptime :</b> {_esc(data.get('uptime', '-'))}",
        f" ┣ ⚙️ <b>Load   :</b> {_esc(data.get('load', '-'))}",
        f" ┣ 🧠 <b>RAM    :</b> {_esc(data.get('ram', '-'))}",
        f" ┣ 💾 <b>Disk   :</b> {_esc(data.get('disk', '-'))}",
    ]
    if data.get("speed_rx") or data.get("speed_tx"):
        lines.append(f" ┣ 🚀 <b>Speed  :</b> <code>{_esc(data.get('speed_rx', '-'))} ↓ / {_esc(data.get('speed_tx', '-'))} ↑ Mbps</code>")
    lines.append(
        f" ┣ 📊 <b>Traffic :</b> Hari Ini: {_esc(data.get('bw_today', '-'))} | Bulan: {_esc(data.get('bw_month', '-'))}"
    )
    status_ok = bool(data.get("panel_active", False))
    lines.append(f" ┗ 🛡️ <b>Status :</b> {_badge(status_ok)}")
    if warn:
        lines.append(" ⚠️ <b>Peringatan:</b> " + " · ".join(warn))
    return "\n".join(lines)


def render_message(master_name: str, reports: list[tuple[str, dict, int]]) -> str:
    """Pesan dashboard lengkap: master di atas, lalu tiap node."""
    parts = [f"🦊 <b>{_esc(master_name.upper())}</b> 🦊"]
    if not reports:
        parts.append("\n<i>Belum ada node yang melapor.</i>")
    for name, data, age in reports:
        parts.append("\n────────────────────────────")
        parts.append(render_block(name, data))
        parts.append(f"<i>⏱️ lapor {age} detik lalu</i>")
    parts.append("\n════════════════════════════")
    parts.append("🌸 <b>Data diperbarui otomatis setiap 60 detik.</b>")
    return "\n".join(parts)
