#!/usr/bin/env python3
"""Harness TEMUAN REVIEW (milik orchestrator) — setiap uji = satu temuan reviewer.

Harness ini SENGAJA MERAH pada artefak sekarang: tiap kegagalan mewakili satu temuan
reviewer yang belum diperbaiki. Coder hanya boleh membuatnya HIJAU dengan mengubah
KODE, bukan berkas uji ini. tests/test_monitorwibu.py (26 uji) tidak boleh diubah.

Jalankan:  python3 tests/test_findings_red.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "lib"))
import monitorwibu  # noqa: E402


def load_script(path: str, name: str):
    """Muat skrip CLI (tanpa ekstensi .py) memakai SourceFileLoader eksplisit.

    Catatan orchestrator: dulu harness ini mengandalkan importlib.machinery.SOURCE_SUFFIXES
    yang dimodifikasi dari dalam lib/monitorwibu.py; itu akomodasi tes di kode produksi dan
    sudah dibuang. Penyesuaian ada di sini, bukan di artefak.
    """
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(REPO, path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


class Dasar(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mrw-review-")
        os.environ["MONITORWIBU_STATE"] = self.tmp
        # Catatan orchestrator: pembatas tingkat modul adalah state GLOBAL; uji yang
        # menyetelnya (mis. B-5) dulu membocorkan batas ke uji berikutnya dan menghasilkan
        # 429 palsu. Setiap uji karena itu mulai dari pembatas yang segar.
        try:
            import api_server as api  # type: ignore
            api.RATE_IP = monitorwibu.RateLimiter(60, 60)
            api.RATE_PRE = monitorwibu.RateLimiter(600, 60)
            api.RATE_NODE = monitorwibu.RateLimiter(30, 60)
            if hasattr(api, "RATE_HEALTH"):
                api.RATE_HEALTH = monitorwibu.RateLimiter(60, 60)
        except Exception:
            pass

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ─────────────────────────── temuan C-1: services non-list mematikan dashboard
class Temuan_Services_Bukan_List(Dasar):
    def test_1_services_angka_ditolak_400(self):
        """C-1: parse_data harus menolak 'services' yang bukan daftar (sekarang lolos)."""
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({"services": 123})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({"services": "xray:down"})

    def test_2_render_tidak_crash_karena_services_ngawur(self):
        """C-1: render tidak boleh meledak walau data node ngawur (satu node jangan menjatuhkan semua)."""
        for jahat in (123, "xray:down", None, {"a": 1}):
            monitorwibu.render_block("SG-1", {"services": jahat, "uptime": "1 Jam"})

    def test_3_cli_list_tidak_crash_karena_services_ngawur(self):
        """C-1: cmd_list harus tahan data ngawur (cetak '-' bila tak berbentuk daftar)."""
        mw = load_script("bin/monitorwibu", "mw_cli")
        mw.CONF = {"NODES_DB": os.path.join(self.tmp, "n.json"), "STATE_DIR": self.tmp}
        mw.NODES_DB, mw.REPORTS, mw.STATE = mw.CONF["NODES_DB"], os.path.join(self.tmp, "nodes"), self.tmp
        db = monitorwibu.NodeDB(mw.NODES_DB); db.add("SG-1")
        monitorwibu.store_report(mw.REPORTS, "SG-1", {"services": 5}, int(time.time()))
        mw.cmd_list()


# ─────────────────────────── temuan C-2: tg() membuang badan galat Telegram
class Temuan_Telegram_Error_Body(Dasar):
    def _mw(self, http_body: bytes, code: int = 400):
        mw = load_script("bin/monitorwibu", "mw_cli2")
        mw.CONF = {"BOT_TOKEN": "123:ABC", "CHAT_ID": "1",
                   "NODES_DB": os.path.join(self.tmp, "n.json"), "STATE_DIR": self.tmp}
        mw.STATE, mw.NODES_DB = self.tmp, mw.CONF["NODES_DB"]

        def fake_urlopen(req, timeout=None, **kw):
            raise urllib.error.HTTPError(req.full_url, code, "Bad Request", {},
                                         io.BytesIO(http_body))
        mw.urllib.request.urlopen = fake_urlopen
        return mw

    def test_1_tg_mempertahankan_pesan_galat_telegram(self):
        """C-2: tg() harus menyertakan isi badan galat Telegram (supaya 'not found' terdeteksi)."""
        body = b'{"ok":false,"error_code":400,"description":"Bad Request: message to edit not found"}'
        res = self._mw(body).tg("editMessageText", chat_id="1", message_id="9", text="x")
        self.assertIn("message to edit not found", str(res.get("description", "")))

    def test_2_render_kirim_pesan_baru_bila_id_pesan_basi(self):
        """C-2: pesan Telegram terhapus -> id dibuang lalu KIRIM pesan baru (regresi v1)."""
        body = b'{"ok":false,"error_code":400,"description":"Bad Request: message to edit not found"}'
        mw = self._mw(body)
        kirim = []
        mw.tg = lambda method, **f: (kirim.append(method) or {"ok": False, "description": "Bad Request: message to edit not found"})
        msg_id_file = os.path.join(self.tmp, "message_id")
        open(msg_id_file, "w").write("77")
        mw.MSG_ID_FILE = msg_id_file
        mw.build_message = lambda: "x"
        mw.cmd_render(send=True)
        self.assertFalse(os.path.exists(msg_id_file), "id pesan basi harus dibuang")
        self.assertIn("sendMessage", kirim, "harus mencoba mengirim pesan baru")


# ─────────────────────────── temuan B-1: batas laju dikonsumsi sebelum verifikasi
class Temuan_Batas_Laju_Sebelum_Verifikasi(Dasar):
    def setUp(self):
        super().setUp()
        import api_server as api  # type: ignore
        self.api = api
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(3, 60)
        db = monitorwibu.NodeDB(api.NODES_DB)
        self.key = db.add("SG-1")
        api._db = db
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def _post(self, body: bytes, name="SG-1", key=None, ts=None, sig=None):
        key = self.key if key is None else key
        ts = int(time.time()) if ts is None else ts
        sig = monitorwibu.sign(key, body) if sig is None else sig
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/report", data=body, method="POST",
            headers={"X-Wibu-Node": name, "X-Wibu-Ts": str(ts), "X-Wibu-Sig": sig})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_node_asli_tidak_bisa_dibungkam_tanpa_kunci(self):
        """B-1: 3 kiriman sampah bertanda tangan PALSU tidak boleh membungkam node asli."""
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        for _ in range(5):
            self._post(body, sig="de" * 32)          # tanpa kunci: harus 403, bukan 429
        code, _ = self._post(body)                    # node asli harus tetap terlayani
        self.assertEqual(code, 200)


# ─────────────────────────── temuan B-2/B-3: ketahanan HTTP
class Temuan_Ketahanan_HTTP(Dasar):
    def setUp(self):
        super().setUp()
        import api_server as api  # type: ignore
        self.api = api
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        db = monitorwibu.NodeDB(api.NODES_DB); self.key = db.add("SG-1"); api._db = db
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close()
        super().tearDown()

    def test_1_handler_punya_batas_waktu(self):
        """B-2: handler wajib punya timeout (slowloris 300 koneksi menggantung)."""
        self.assertIsNotNone(getattr(self.api.Handler, "timeout", None))

    def test_2_balasan_413_menutup_koneksi(self):
        """B-3: 413 tanpa membaca badan harus menutup koneksi (cegah desync/request smuggling)."""
        raw = (os.urandom(0).hex(),)
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        hdr = (f"POST /api/report HTTP/1.1\r\nHost: x\r\nX-Wibu-Node: SG-1\r\n"
               f"X-Wibu-Ts: {int(time.time())}\r\nX-Wibu-Sig: {'ab'*32}\r\n"
               f"Content-Length: {monitorwibu.MAX_BODY + 100}\r\n\r\n").encode()
        s.sendall(hdr + b"A" * 200)
        s.settimeout(3)
        try:
            data = s.recv(4096).decode(errors="replace")
        except socket.timeout:
            data = ""
        s.close()
        self.assertIn("413", data)
        self.assertIn("close", data.lower(), "harus ada Connection: close pada 413")


# ─────────────────────────── temuan B-4/C-6: nodes.json rusak
class Temuan_Nodes_DB_Rusak(Dasar):
    def test_1_load_tidak_meledak_pada_berkas_rusak(self):
        """B-4: NodeDB.load harus menoleransi nodes.json rusak (jangan bikin server gagal start)."""
        path = os.path.join(self.tmp, "nodes.json")
        open(path, "w").write('{"SG-1": "abc"')
        db = monitorwibu.NodeDB(path)
        self.assertEqual(db.nodes, {})

    def test_2_permintaan_tetap_dijawab_saat_daftar_kunci_rusak(self):
        """B-4: nodes.json rusak saat berjalan -> balas galat HTTP, bukan memutus koneksi."""
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB); key = db.add("SG-1"); api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            open(api.NODES_DB, "w").write("[")
            body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
                headers={"X-Wibu-Node": "SG-1", "X-Wibu-Ts": str(int(time.time())),
                         "X-Wibu-Sig": monitorwibu.sign(key, body)})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    code = r.status
            except urllib.error.HTTPError as e:
                code = e.code
            self.assertGreaterEqual(code, 400)
        finally:
            srv.shutdown(); srv.server_close()


# ─────────────────────────── temuan B-5..B-11 (minor keamanan)
class Temuan_Minor_Keamanan(Dasar):
    def test_1_del_node_menolak_nama_traversal(self):
        """B-8: 'del-node' harus memvalidasi nama (terbukti menghapus berkas di luar dir laporan)."""
        canary = os.path.join(self.tmp, "canary.json")
        open(canary, "w").write("{}")
        conf = os.path.join(self.tmp, "m.conf")
        open(conf, "w").write(f'NODES_DB="{os.path.join(self.tmp,"n.json")}"\nSTATE_DIR="{self.tmp}"\n')
        env = dict(os.environ, MONITORWIBU_CONF=conf, PYTHONDONTWRITEBYTECODE="1")
        subprocess.run([sys.executable, "-B", os.path.join(REPO, "bin/monitorwibu"), "del-node",
                        "../../canary"], env=env, capture_output=True)
        self.assertTrue(os.path.exists(canary), "berkas di luar dir laporan terhapus!")

    def test_2_vnstat_dibaca_sebagai_total(self):
        """B-10: 'Traffic Hari Ini/Bulan' harus TOTAL (field 6/11), bukan laju kbit/s."""
        stub = os.path.join(self.tmp, "vnstat")
        open(stub, "w").write('#!/bin/sh\necho "1;eno1;2021-11-29;6.02 GiB;0.99 GiB;7.00 GiB;738.84 kbit/s;2021-11;6.02 GiB;0.99 GiB;7.00 GiB;24.06 kbit/s;6.02 GiB;0.99 GiB;7.00 GiB"\n')
        os.chmod(stub, 0o755)
        node = load_script("bin/monitorwibu-node", "mrw_node")
        node.CACHE_DIR = self.tmp
        old = os.environ["PATH"]; os.environ["PATH"] = self.tmp + ":" + old
        try:
            today, month = node.traffic()
        finally:
            os.environ["PATH"] = old
        self.assertIn("GiB", today, f"harus total GiB, dapat: {today!r}")
        self.assertIn("GiB", month, f"harus total GiB, dapat: {month!r}")

    def test_3_ip_ipv6_tetap_disamarkan(self):
        """B-11: mask_ip harus menyamarkan IPv6 juga (sekarang utuh)."""
        node = load_script("bin/monitorwibu-node", "mrw_node2")
        async_none = None
        self.assertNotEqual(node.mask_ip("2001:db8:1::5"), "2001:db8:1::5")
        self.assertTrue(async_none is None)

    def test_4_balasan_403_seragam(self):
        """B-13: badan 403 jangan membedakan 'unknown node' vs 'bad signature' (oracle enumerasi)."""
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB); key = db.add("SG-1"); api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            body = b"{}"
            def post(name, sig):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
                    headers={"X-Wibu-Node": name, "X-Wibu-Ts": str(int(time.time())),
                             "X-Wibu-Sig": sig})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        return r.read().decode()
                except urllib.error.HTTPError as e:
                    return e.read().decode()
            a = post("ADA-TAPI-SALAH", "cd" * 32)
            b = post("BELUM-TERDAFTAR", "cd" * 32)
            self.assertEqual(a, b, "badan 403 membedakan node terdaftar vs tidak")
        finally:
            srv.shutdown(); srv.server_close()

    def test_5_rate_limiter_membatasi_memori(self):
        """B-14: ember RateLimiter jangan tumbuh tanpa batas (kunci dari header penyerang)."""
        rl = monitorwibu.RateLimiter(5, 60)
        for i in range(20000):
            rl.allow(f"k{i}", now=time.time())
        self.assertLess(len(rl.hits), 5000, f"hits={len(rl.hits)} tidak dibatasi")

    def test_6_health_dibatasi_laju(self):
        """B-5: /health pra-autentikasi harus dibatasi laju (sekarang bebas).

        Catatan orchestrator: batas ini berpindah objek pada putaran 4 — /health kini
        memakai pembatasnya sendiri (`RATE_HEALTH`) agar namespace '/health' tidak bisa
        memenuhi tabel ember milik laporan node (temuan B2). Karena itu uji ini menyetel
        KEDUA objek; perilaku yang diuji tetap sama: /health harus 429 setelah kuotanya habis.
        """
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(5, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(1000, 60)
        if hasattr(api, "RATE_HEALTH"):
            api.RATE_HEALTH = monitorwibu.RateLimiter(5, 60)
        api._db = monitorwibu.NodeDB(api.NODES_DB)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            kode = set()
            for _ in range(10):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                        kode.add(r.status)
                except urllib.error.HTTPError as e:
                    kode.add(e.code)
            self.assertIn(429, kode, f"tanpa batas laju: {kode}")
        finally:
            srv.shutdown(); srv.server_close()


# ─────────────────────────── temuan B-6/B-7, C-5: payload non-objek
class Temuan_Payload(Dasar):
    def test_badan_json_bukan_objek_dibalas_400(self):
        """C-5: JSON sah tapi bukan objek (list/str/int) harus 400, bukan memutus koneksi."""
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB); key = db.add("SG-1"); api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for body in (b"[1,2,3]", b'"teks"', b"123"):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
                    headers={"X-Wibu-Node": "SG-1", "X-Wibu-Ts": str(int(time.time())),
                             "X-Wibu-Sig": monitorwibu.sign(key, body)})
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req, timeout=10)
                self.assertEqual(cm.exception.code, 400, f"badan {body!r}")
        finally:
            srv.shutdown(); srv.server_close()


# ─────────────────────────── temuan C-3/C-4/C-7, A-9/A-19/A-24
class Temuan_Fitur_Dan_Dokumen(unittest.TestCase):
    def test_1_load_ditampilkan(self):
        """C-4: 'load' dikumpulkan node tetapi tidak pernah dirender."""
        teks = monitorwibu.render_block("SG-1", {"load": "0.30 / 2 core", "uptime": "1 Jam"})
        self.assertIn("Load", teks)

    def test_2_master_memonitor_dirinya_sendiri(self):
        """C-3 (regresi v1): master ikut jadi node yang dilaporkan."""
        isi = open(os.path.join(REPO, "install-master.sh")).read()
        self.assertIn("node.conf", isi, "master harus membuat node.conf untuk dirinya")
        self.assertIn("monitorwibu-node.timer", isi, "timer node harus diaktifkan di master")

    def test_3_batas_300_detik_konsisten(self):
        """C-7: tepat 300 detik harus konsisten antara baca & pangkas."""
        tmp = tempfile.mkdtemp()
        try:
            ts = int(time.time()) - 300
            monitorwibu.store_report(tmp, "TEPAT", {"uptime": "x"}, ts)
            dibaca = [n for n, _, _ in monitorwibu.read_reports(tmp, fresh=300)]
            monitorwibu.prune_reports(tmp, fresh=300)
            masih = os.path.exists(os.path.join(tmp, "TEPAT.json"))
            self.assertEqual(bool(dibaca), masih, "baca & pangkas tidak sepakat di batas 300s")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_4_handoff_tidak_menyatakan_repo_tanpa_sumber(self):
        """A-19: HANDOFF masih melarang sumber masuk repo publik, padahal repo publik kini memuat sumber."""
        isi = open(os.path.join(REPO, "HANDOFF_v2_FOR_AI.md")).read()
        self.assertNotIn("sengaja TANPA sumber", isi)
        self.assertIn("publik", isi.lower())

    def test_5_installer_tidak_membuat_dir_kosong(self):
        """A-26: install-master.sh membuat $LIBDIR/tests yang tak pernah diisi."""
        isi = open(os.path.join(REPO, "install-master.sh")).read()
        self.assertNotIn("LIBDIR/tests", isi)

    def test_6_penolakan_node_tak_terdaftar_dicatat(self):
        """A-9: README bilang penolakan tercatat (unknown node) — kenyataannya tidak dicatat."""
        isi = open(os.path.join(REPO, "lib/api_server.py")).read()
        cuplikan = isi.split("if not key:")[1][:200] if "if not key:" in isi else ""
        self.assertIn("log_message", cuplikan, "penolakan 'unknown node' tidak dicatat ke log")


# ─────────── TEMUAN PUTARAN 2 (falsifier): klaim yang tembus dan harus ditutup
class Temuan_Putaran2(Dasar):
    def _api(self, rate_ip=None, rate_node=None):
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = rate_ip or monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = rate_node or monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB)
        key = db.add("SG-1")
        api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return api, port, key

    def _post(self, port, body: bytes, name="SG-1", key=None, sig=None):
        sig = ("ef" * 32) if sig is None else sig
        if key is not None:
            sig = monitorwibu.sign(key, body)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
            headers={"X-Wibu-Node": name, "X-Wibu-Ts": str(int(time.time())),
                     "X-Wibu-Sig": sig})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_E2A1_ip_bucket_pra_verifikasi_tidak_membungkam_node_sah(self):
        """E2-A1 (MAJOR): 60 kiriman sampah dari satu IP tidak boleh membungkam node sah se-IP itu."""
        _, port, key = self._api(rate_ip=monitorwibu.RateLimiter(60, 60),
                                 rate_node=monitorwibu.RateLimiter(1000, 60))
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        self.assertEqual(self._post(port, body, key=key), 200)
        for _ in range(60):
            self.assertEqual(self._post(port, body, name="SG-1", sig="ef" * 32), 403)
        self.assertEqual(self._post(port, body, key=key), 200,
                         "node sah terlayani setelah hujan permintaan sampah se-IP")

    def test_E2D_eviksi_tidak_mereset_ember_aktif(self):
        """E2-D: batas tidak bisa ditembus dengan mengganti-ganti kunci (eviksi tidak mereset ember aktif)."""
        rl = monitorwibu.RateLimiter(30, 60)
        rl.MAX_BUCKETS = 64
        now = time.time()
        for _ in range(30):
            rl.allow("KORBAN", now=now)
        for i in range(300):
            rl.allow(f"dummy{i}", now=now + 1)
        self.assertFalse(rl.allow("KORBAN", now=now), "ember aktif ter-reset oleh eviksi")
        self.assertLessEqual(len(rl.hits), 65, "memori ember melebihi batas")

    def test_E2F2_laporan_dengan_data_bukan_objek_tidak_melumpuhkan_dashboard(self):
        """E2-F2: satu berkas laporan dengan data bukan objek tidak boleh mematikan render semua node."""
        nodes = os.path.join(self.tmp, "nodes")
        os.makedirs(nodes, exist_ok=True)
        with open(os.path.join(nodes, "RUSAK.json"), "w") as f:
            json.dump({"ts": time.time(), "data": ["bukan", "objek"]}, f)
        reports = monitorwibu.read_reports(nodes, fresh=300)
        monitorwibu.render_message("MASTER", reports)          # tidak boleh melempar
        for isi in (123, None, "teks", {"a": 1}):
            monitorwibu.render_block("N", isi)

    def test_E2J1_installer_tidak_menulis_node_conf_tanpa_kunci(self):
        """E2-J1: bila add-node gagal, jangan tulis node.conf dengan KEY kosong + jangan nyalakan timer."""
        isi = open(os.path.join(REPO, "install-master.sh")).read()
        mulai = isi.index("if [[ ! -f $ETC/node.conf ]]")
        akhir = isi.index('} > "$ETC/node.conf"', mulai)
        blok_tulis = isi[mulai:akhir]
        self.assertTrue(("-n $MS_KEY" in blok_tulis) or ('-n "$MS_KEY"' in blok_tulis),
                        "tidak ada penjaga 'kunci tidak kosong' sebelum menulis node.conf")
        sisa = isi[akhir:]
        penjaga_timer = ("-s $ETC/node.conf" in sisa[:600]) or ("-n $MS_KEY" in sisa[:600]) \
                        or ("-s \"$ETC/node.conf\"" in sisa[:600])
        self.assertTrue(penjaga_timer,
                        "timer node dinyalakan tanpa memastikan node.conf berisi kunci")

    def test_E2J3_master_name_tidak_disuntik_ke_python_c(self):
        """E2-J3: MASTER_NAME tidak boleh disisipkan ke dalam string python -c (terbukti eksekusi kode)."""
        isi = open(os.path.join(REPO, "install-master.sh")).read()
        self.assertNotIn("get('$MASTER_NAME'", isi, "MASTER_NAME disuntikkan ke string python -c")
        self.assertRegex(isi, r"sys\.argv", "seharusnya nilai dilewatkan lewat argv, bukan interpolasi")

    def test_E2E_nodes_db_rusak_tidak_senyap(self):
        """E2-E: nodes.json rusak saat start harus memberi pesan (jangan kehilangan layanan senyap)."""
        import contextlib
        path = os.path.join(self.tmp, "nodes.json")
        with open(path, "w") as f:
            f.write('{"SG-1": ')
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            db = monitorwibu.NodeDB(path)
        self.assertEqual(db.nodes, {})
        self.assertTrue(buf.getvalue().strip(), "tidak ada pesan apa pun saat daftar kunci rusak")


# ─────────── TEMUAN PUTARAN 3 (konformansi + falsifier): sisa jalur & overclaim
class Temuan_Putaran3(Dasar):
    def _api(self, rate_ip=None, rate_node=None, rate_pre=None):
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = rate_ip or monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = rate_node or monitorwibu.RateLimiter(1000, 60)
        if rate_pre is not None:
            api.RATE_PRE = rate_pre
        db = monitorwibu.NodeDB(api.NODES_DB)
        key = db.add("SG-1")
        api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return api, port, key

    def _raw(self, port, payload: bytes, detik=3) -> bytes:
        """Kirim byte mentah, kembalikan seluruh balasan sampai koneksi ditutup."""
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(payload)
        s.settimeout(detik)
        out = b""
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                out += b
        except Exception:
            pass
        s.close()
        return out

    def _post_ok(self, port, key):
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
            headers={"X-Wibu-Node": "SG-1", "X-Wibu-Ts": str(int(time.time())),
                     "X-Wibu-Sig": monitorwibu.sign(key, body)})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_R4_1_balasan_404_membuang_badan(self):
        """F8: 404 pada POST berbadan tidak boleh menyisakan badan -> desync/501."""
        _, port, _ = self._api()
        raw = self._raw(port, b"POST /bukan-api HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nHELLO"
                              b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"404", raw, "seharusnya 404")
        self.assertNotIn(b"501", raw, "sisa badan menyatu dengan request berikutnya (desync)")

    def test_R4_2_balasan_429_pra_auth_membuang_badan(self):
        """F8: 429 pra-auth tidak boleh menyisakan badan -> desync/501."""
        _, port, key = self._api(rate_pre=monitorwibu.RateLimiter(1, 60))
        self.assertEqual(self._post_ok(port, key), 200)          # habiskan kuota pra-auth
        raw = self._raw(port, b"POST /api/report HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n"
                              b"X-Wibu-Node: SG-1\r\nX-Wibu-Ts: 1\r\nX-Wibu-Sig: ff\r\n\r\nHELLO"
                              b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b"429", raw, "seharusnya 429 (batas pra-auth)")
        self.assertNotIn(b"501", raw, "sisa badan menyatu dengan request berikutnya (desync)")

    def test_R4_3_health_punya_kuota_sendiri(self):
        """A4: /health tidak boleh memakai kuota RATE_IP yang sama dengan laporan node sah."""
        _, port, key = self._api(rate_ip=monitorwibu.RateLimiter(60, 60))
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10):
                    pass
            except urllib.error.HTTPError:
                pass
        self.assertEqual(self._post_ok(port, key), 200,
                         "60 panggilan /health dari IP yang sama membungkam laporan node sah")

    def test_R4_4_registry_bukan_objek_dilaporkan(self):
        """D2: nodes.json sah tapi bukan objek (null/[]) tidak boleh diterima senyap."""
        import contextlib
        path = os.path.join(self.tmp, "nodes.json")
        with open(path, "w") as f:
            f.write("null")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            db = monitorwibu.NodeDB(path)
        self.assertTrue(buf.getvalue().strip(), "registry non-objek diterima tanpa pesan")
        api, port, _ = self._api()
        api._db = db
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            body = json.loads(r.read().decode())
        self.assertIn("registry", body, "/health tidak melaporkan status registry")
        self.assertNotEqual(body["registry"], "ok", "/health mengaku sehat padahal registry tak terpakai")
        db2 = monitorwibu.NodeDB(os.path.join(self.tmp, "healthy.json"))
        db2.add("SG-1")
        api._db = db2
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            body = json.loads(r.read().decode())
        self.assertEqual(body.get("registry"), "ok", "registry sehat tidak dilaporkan ok")

    def test_R4_5_master_name_divalidasi_sebelum_ditulis(self):
        """E3b: MASTER_NAME harus divalidasi sebelum ditulis ke node.conf (anti injeksi baris)."""
        isi = open(os.path.join(REPO, "install-master.sh")).read()
        pola = 'NAME=\\"$MASTER_NAME\\"'
        if pola not in isi:
            return                                   # ditulis lewat jalur lain yang aman
        pre = isi[:isi.rindex(pola)]
        self.assertTrue(("MASTER_NAME =~" in pre) or ("NAME_RE" in pre),
                        "MASTER_NAME ditulis ke node.conf tanpa divalidasi (injeksi baris)")


# ─────────── TEMUAN PUTARAN 4 (falsifier): konsekuensi perbaikan putaran 3
class Temuan_Putaran4(Dasar):
    def _api(self, rate_ip=None, rate_node=None, rate_pre=None, ip_klien=None):
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = rate_ip or monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = rate_node or monitorwibu.RateLimiter(1000, 60)
        if rate_pre is not None:
            api.RATE_PRE = rate_pre
        db = monitorwibu.NodeDB(api.NODES_DB)
        key = db.add("SG-1")
        api._db = db
        handler = api.Handler
        if ip_klien:                       # paksa IP klien seolah pelapor dari jauh
            class H(api.Handler):
                def setup(self):
                    super().setup()
                    self.client_address = (ip_klien, 5555)
            handler = H
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return api, port, key

    def _post_ok(self, port, key, nama="SG-1"):
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
            headers={"X-Wibu-Node": nama, "X-Wibu-Ts": str(int(time.time())),
                     "X-Wibu-Sig": monitorwibu.sign(key, body)})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _health(self, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            return json.loads(r.read().decode())

    def test_P4_A1_badan_punya_deadline_total(self):
        """A1: klien yang meneteskan badan (1 byte / 5 detik) tidak boleh menahan thread selamanya.

        Catatan orchestrator: `recv` timeout = server MASIH menunggu (bukan lepas) — hanya
        balasan atau koneksi tertutup yang dihitung sebagai 'server melepas thread'.
        """
        _, port, _ = self._api()
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /api/report HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n"
                  b"X-Wibu-Node: SG-1\r\nX-Wibu-Ts: 1\r\nX-Wibu-Sig: " + b"ef" * 32 + b"\r\n\r\n")
        s.settimeout(0.5)
        t0 = time.time()
        lepas = False
        while time.time() - t0 < 22:
            time.sleep(5)                       # jeda < timeout per-recv (10s), total > deadline
            try:
                s.sendall(b"E")
            except OSError:
                lepas = True
                break
            try:
                b = s.recv(4096)
            except socket.timeout:
                continue                        # server masih menunggu badan
            except OSError:
                lepas = True
                break
            lepas = True
            break
        elapsed = time.time() - t0
        s.close()
        self.assertLess(elapsed, 14, f"thread ditahan {elapsed:.1f}s oleh badan yang menetes")
        self.assertTrue(lepas, "server membiarkan koneksi menetes tanpa batas total")

    def test_P4_A4_content_length_ganda_ditolak(self):
        """A4: Content-Length ganda harus ditolak 400 + tutup koneksi (jangan sisa badan jadi request)."""
        _, port, _ = self._api()
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /api/report HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\nContent-Length: 1000\r\n\r\n"
                  + b"A" * 1000 + b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
        s.settimeout(3)
        out = b""
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                out += b
        except Exception:
            pass
        s.close()
        self.assertEqual(out.count(b"HTTP/1.1"), 1,
                         f"lebih dari satu balasan: sisa badan diurai sebagai request ({out[:120]!r})")
        self.assertIn(b"400", out)

    def test_P4_B2_health_tidak_memenuhi_tabel_laporan(self):
        """B2: lalu lintas /health tidak boleh memakai tabel ember milik laporan node sah.

        Properti yang diukur (bukan nama variabel): setelah /health dipanggil melewati
        kuotanya, tabel pembatas laporan harus tetap kosong dan laporan node sah dari IP
        yang belum pernah melapor tetap dilayani.
        """
        api, port, key = self._api(rate_ip=monitorwibu.RateLimiter(60, 60), ip_klien="203.0.113.9")
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10):
                    pass
            except urllib.error.HTTPError:
                pass
        self.assertEqual(len(api.RATE_IP.hits), 0,
                         f"lalu lintas /health tercatat di tabel pembatas laporan: {list(api.RATE_IP.hits)[:3]}")
        self.assertEqual(self._post_ok(port, key), 200,
                         "laporan node sah dari IP baru ditolak karena tabel pembatas laporan terisi")

    def test_P4_C2_damaged_tidak_basi(self):
        """C2: nodes.json rusak saat berjalan -> /health tidak boleh mengaku registry sehat."""
        api, port, key = self._api()
        self.assertEqual(self._post_ok(port, key), 200)
        self.assertEqual(self._health(port).get("registry"), "ok")
        with open(api.NODES_DB, "w") as f:
            f.write("{")                        # rusak saat server berjalan
        self.assertEqual(self._post_ok(port, key), 503)
        self.assertNotEqual(self._health(port).get("registry"), "ok",
                            "registry diblok 503 tetapi /health tetap melaporkan ok")

    def test_P4_C2b_mtime_identik_tetap_terdeteksi(self):
        """C2b: perubahan isi nodes.json dengan mtime identik harus tetap terdeteksi."""
        api, port, key = self._api()
        self.assertEqual(self._post_ok(port, key), 200)
        m = api.db_changed.mtime
        with open(api.NODES_DB, "w") as f:
            f.write("{")
        os.utime(api.NODES_DB, (m, m))
        self.assertEqual(os.path.getmtime(api.NODES_DB), m, "prasyarat uji: mtime identik tidak tercapai")
        self.assertEqual(self._post_ok(port, key), 503,
                         "berkas rusak dengan mtime sama dilewati -> memakai daftar kunci lama")


# ─────────── TEMUAN PUTARAN 5 (falsifier): fase header, kesehatan basi, biaya baca
class Temuan_Putaran5(Dasar):
    def _api(self, rate_ip=None, rate_node=None, ip_klien=None):
        import api_server as api  # type: ignore
        api.REPORTS_DIR = os.path.join(self.tmp, "nodes")
        api.NODES_DB = os.path.join(self.tmp, "nodes.json")
        api.RATE_IP = rate_ip or monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = rate_node or monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB)
        key = db.add("SG-1")
        api._db = db
        srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        return api, port, key

    def _post_ok(self, port, key):
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/report", data=body, method="POST",
            headers={"X-Wibu-Node": "SG-1", "X-Wibu-Ts": str(int(time.time())),
                     "X-Wibu-Sig": monitorwibu.sign(key, body)})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _health(self, port):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as r:
            return json.loads(r.read().decode())

    def test_P5_F1_header_tidak_menahan_thread(self):
        """F1: klien yang meneteskan HEADER (1 byte / 5 detik) tidak boleh menahan thread selamanya."""
        _, port, _ = self._api()
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"POST /api/report HTTP/1.1\r\nHost: x\r\n")     # header belum selesai
        s.settimeout(0.5)
        t0 = time.time()
        lepas = False
        while time.time() - t0 < 22:
            time.sleep(5)
            try:
                s.sendall(b"X")
            except OSError:
                lepas = True
                break
            try:
                s.recv(4096)
            except socket.timeout:
                continue                        # server masih menunggu header
            except OSError:
                lepas = True
                break
            lepas = True
            break
        elapsed = time.time() - t0
        s.close()
        self.assertLess(elapsed, 14, f"thread ditahan {elapsed:.1f}s oleh header yang menetes")
        self.assertTrue(lepas, "server membiarkan header menetes tanpa anggaran waktu")

    def test_P5_F2_health_menyegarkan_status_registry(self):
        """F2: /health harus menyegarkan status registry sendiri, tanpa menunggu POST."""
        api, port, key = self._api()
        self.assertEqual(self._post_ok(port, key), 200)
        self.assertEqual(self._health(port).get("registry"), "ok")
        with open(api.NODES_DB, "w") as f:
            f.write("{")                        # rusak di disk, belum ada POST sesudahnya
        self.assertEqual(self._health(port).get("registry"), "damaged",
                         "/health melaporkan ok padahal nodes.json rusak")
        with open(api.NODES_DB, "w") as f:
            json.dump({"SG-1": api._db.get("SG-1")}, f)
        self.assertEqual(self._health(port).get("registry"), "ok",
                         "/health tetap damaged padahal nodes.json sudah sehat")

    def test_P5_F3_tidak_membaca_ulang_berkas_tiap_permintaan(self):
        """F3: permintaan tanpa perubahan berkas tidak boleh membaca ulang seluruh nodes.json."""
        api, port, key = self._api()
        self.assertEqual(self._post_ok(port, key), 200)      # permintaan pertama boleh membaca
        import builtins
        asli = builtins.open
        hitung = {"n": 0}

        def hitung_open(*a, **k):
            if a and str(a[0]).endswith("nodes.json"):
                hitung["n"] += 1
            return asli(*a, **k)

        builtins.open = hitung_open
        try:
            for _ in range(5):
                self.assertEqual(self._post_ok(port, key), 200)
        finally:
            builtins.open = asli
        self.assertLessEqual(hitung["n"], 2,
                             f"nodes.json dibaca ulang {hitung['n']}x dalam 5 permintaan tanpa perubahan")


if __name__ == "__main__":
    unittest.main(verbosity=2)
