#!/usr/bin/env python3
"""Uji MONITOR WIBU v2 — tanpa dependensi luar.

    python3 tests/test_monitorwibu.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import monitorwibu  # noqa: E402


class TestNamaData(unittest.TestCase):
    def test_nama_sah(self):
        for nama in ("SG-1", "id_jkt_2", "A" * 50):
            self.assertEqual(monitorwibu.sanitize_name(nama), nama)

    def test_nama_tidak_sah(self):
        for nama in ("", "  ", "ada spasi", "../etc/passwd", "A" * 51, "nama/turun", "emoji🦊"):
            with self.assertRaises(monitorwibu.Invalid, msg=nama):
                monitorwibu.sanitize_name(nama)

    def test_data_batas(self):
        self.assertEqual(monitorwibu.parse_data({"uptime": "1 Jam", "ram_pct": 42.5, "ok": True}),
                         {"uptime": "1 Jam", "ram_pct": 42.5, "ok": True})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({"k": "x" * 401})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({f"k{i}": 1 for i in range(41)})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({"k": "baris\nbaru\ttab\x07bel"})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data({"k": ["x" * 101]})
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.parse_data("bukan dict")


class TestTandaTangan(unittest.TestCase):
    def test_terima(self):
        key, body, ts = "abc123", b'{"data":{"uptime":"1 Jam"}}', int(time.time())
        sig = monitorwibu.sign(key, body)
        self.assertEqual(monitorwibu.verify(key, body, sig, ts), (True, "ok"))

    def test_tolak_kunci_lain(self):
        body, ts = b"{}", int(time.time())
        sig = monitorwibu.sign("kunci-A", body)
        self.assertEqual(monitorwibu.verify("kunci-B", body, sig, ts)[1], "sig")

    def test_tolak_badan_diubah(self):
        ts = int(time.time())
        sig = monitorwibu.sign("k", b'{"data":{"uptime":"1 Jam"}}')
        self.assertEqual(monitorwibu.verify("k", b'{"data":{"uptime":"9 Jam"}}', sig, ts)[1], "sig")

    def test_tolak_waktu_basi_dan_masa_depan(self):
        body, key = b"{}", "k"
        sig = monitorwibu.sign(key, body)
        now = int(time.time())
        self.assertEqual(monitorwibu.verify(key, body, sig, now - 91, now=now)[1], "ts-window")
        self.assertEqual(monitorwibu.verify(key, body, sig, now + 91, now=now)[1], "ts-window")
        self.assertTrue(monitorwibu.verify(key, body, sig, now - 89, now=now)[0])

    def test_tolak_format_sig(self):
        now = int(time.time())
        for bad in ("", "zz" * 32, "abc", "A" * 64):
            self.assertEqual(monitorwibu.verify("k", b"{}", bad, now, now=now)[1], "sig-format")


class TestPembatasLaju(unittest.TestCase):
    def test_batasi(self):
        rl = monitorwibu.RateLimiter(3, 60)
        now = 1_000_000.0
        self.assertTrue(all(rl.allow("ip", now=t) for t in (now, now, now)))
        self.assertFalse(rl.allow("ip", now=now))
        self.assertTrue(rl.allow("ip", now=now + 61))

    def test_terpisah_per_kunci(self):
        rl = monitorwibu.RateLimiter(1, 60)
        self.assertTrue(rl.allow("a", now=0))
        self.assertFalse(rl.allow("a", now=0))
        self.assertTrue(rl.allow("b", now=0))


class TestRender(unittest.TestCase):
    def test_escape_html(self):
        pesan = monitorwibu.render_message("master", [("SG-1", {
            "city": "<b>Jakarta</b> & <script>alert(1)</script>",
            "services": ["xray:down"], "panel_active": False,
            "ram_pct": 95, "disk_pct": 91,
        }, 3)])
        self.assertNotIn("<script>", pesan)
        self.assertIn("&lt;script&gt;", pesan)
        self.assertIn("&amp;", pesan)
        self.assertIn("Peringatan", pesan)
        self.assertIn("xray", pesan)

    def test_blok_normal(self):
        pesan = monitorwibu.render_block("SG-1", {"city": "Singapore", "uptime": "2 Hari, 03:04",
                                           "ram": "1.0 GB / 2.0 GB (50%)", "disk_pct": 40,
                                           "bw_today": "1.2 GB", "services": ["xray:up"],
                                           "panel_active": True})
        self.assertIn("ACTIVE", pesan)
        self.assertNotIn("Peringatan", pesan)

    def test_belum_ada_node(self):
        self.assertIn("Belum ada node", monitorwibu.render_message("m", []))


class TestPenyimpanan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_simpan_baca_pangkas(self):
        monitorwibu.store_report(self.dir, "SG-1", {"uptime": "1 Jam"}, int(time.time()))
        monitorwibu.store_report(self.dir, "ID-2", {"uptime": "2 Jam"}, int(time.time()) - 10_000)
        segar = monitorwibu.read_reports(self.dir, fresh=300)
        self.assertEqual([n for n, _, _ in segar], ["SG-1"])
        self.assertEqual(monitorwibu.prune_reports(self.dir, fresh=300), 1)
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_nama_berkas_aman(self):
        monitorwibu.store_report(self.dir, "SG-1", {}, int(time.time()))
        self.assertEqual(os.listdir(self.dir), ["SG-1.json"])
        with self.assertRaises(monitorwibu.Invalid):
            monitorwibu.store_report(self.dir, "../../etc/passwd", {}, int(time.time()))

    def test_basis_kunci(self):
        path = os.path.join(self.dir, "nodes.json")
        db = monitorwibu.NodeDB(path)
        key = db.add("SG-1")
        self.assertEqual(len(key), 64)
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), oct(0o600))
        self.assertEqual(monitorwibu.NodeDB(path).get("SG-1"), key)
        self.assertTrue(db.remove("SG-1"))
        self.assertEqual(monitorwibu.NodeDB(path).nodes, {})


class TestInteropShell(unittest.TestCase):
    """Klien shell (openssl) harus menghasilkan tanda tangan yang sama dengan Python."""

    @unittest.skipUnless(shutil.which("openssl"), "openssl tidak ada")
    def test_openssl_hmac_sama(self):
        key, body = "kunci-uji", b'{"data":{"uptime":"1 Jam"}}'
        out = subprocess.run(
            ["openssl", "dgst", "-sha256", "-hmac", key, "-binary"],
            input=body, capture_output=True).stdout
        shell_sig = subprocess.run(["od", "-An", "-tx1"], input=out,
                                   capture_output=True).stdout.decode().split()
        self.assertEqual("".join(shell_sig), monitorwibu.sign(key, body))


class TestHttpEndToEnd(unittest.TestCase):
    """Server sungguhan dinyalakan di port acak; kirim laporan bertanda tangan betulan."""

    @classmethod
    def setUpClass(cls):
        import api_server as api  # noqa: PLC0415
        cls.api = api
        cls.tmp = tempfile.mkdtemp()
        api.REPORTS_DIR = os.path.join(cls.tmp, "nodes")
        api.NODES_DB = os.path.join(cls.tmp, "nodes.json")
        api.RATE_IP = monitorwibu.RateLimiter(1000, 60)
        api.RATE_NODE = monitorwibu.RateLimiter(1000, 60)
        db = monitorwibu.NodeDB(api.NODES_DB)
        cls.key = db.add("SG-1")
        api._db = db
        from http.server import ThreadingHTTPServer  # noqa: PLC0415
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.port = cls.srv.server_address[1]
        cls.t = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

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
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def test_1_laporan_sah(self):
        body = json.dumps({"data": {"uptime": "1 Jam", "ram_pct": 10}}).encode()
        code, text = self._post(body)
        self.assertEqual((code, text), (200, "OK"))
        berkas = os.path.join(self.api.REPORTS_DIR, "SG-1.json")
        self.assertTrue(os.path.exists(berkas))
        self.assertEqual(json.load(open(berkas))["data"]["uptime"], "1 Jam")

    def test_2_tanpa_tanda_tangan(self):
        self.assertEqual(self._post(b"{}", sig="")[0], 403)

    def test_3_node_tidak_dikenal(self):
        body = b"{}"
        code, _ = self._post(body, name="BELUM-ADA", key="x" * 64,
                             sig=monitorwibu.sign("x" * 64, body))
        self.assertEqual(code, 403)

    def test_4_ulang_terlambat(self):
        body = json.dumps({"data": {"uptime": "1 Jam"}}).encode()
        self.assertEqual(self._post(body, ts=int(time.time()) - 600)[0], 403)

    def test_5_badan_bukan_json(self):
        self.assertEqual(self._post(b"bukan json")[0], 400)

    def test_6_terlalu_besar(self):
        body = b'{"data":{"x":"' + b"A" * (monitorwibu.MAX_BODY + 10) + b'"}}'
        self.assertEqual(self._post(body)[0], 413)

    def test_7_field_tidak_sah(self):
        body = json.dumps({"data": {"k": "x" * 500}}).encode()
        self.assertEqual(self._post(body)[0], 400)

    def test_8_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=5) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read().decode())["ok"])

    def test_9_jalur_lain_404(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/lain")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=1)
