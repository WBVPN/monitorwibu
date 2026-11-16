# HANDOFF MONITOR WIBU v2 — UNTUK AI / AGEN BERIKUTNYA

Tanggal : 2026-11-16
Paket   : MONITOR WIBU v2 (perbaikan dari v3.0 / 2026-09-27)
Asal    : sumber v1 dipulihkan dari riwayat git `WBVPN/monitorwibu` (commit `78cece0`,
          tepat sebelum `24a1c49` yang mengeluarkan sumber dari repo; repo pernah dihapus
          pemilik lalu di-restore dari GitHub "Deleted repositories" → 48 commit utuh)
Bahasa  : shell (instalasi) + Python 3 stdlib (server, agen, CLI) — **tanpa pip/Flask**

---

## 1. Arsitektur ringkas

```
VPS node (tiap menit, systemd timer monitorwibu-node.timer)
   bin/monitorwibu-node  ->  kumpulkan metrik  ->  POST /api/report
      header X-Wibu-Node / X-Wibu-Ts / X-Wibu-Sig (HMAC-SHA256 atas badan JSON)
VPS master
   lib/api_server.py  -> verifikasi kunci per node + jendela 90s + batas laju
                      -> simpan /var/lib/monitorwibu/nodes/<NAMA>.json (atomik)
   bin/monitorwibu render  -> susun pesan -> sendMessage / editMessageText
      (id pesan disimpan di /var/lib/monitorwibu/message_id → satu pesan terus diperbarui)
```

Konfigurasi: `/etc/monitorwibu/master.conf` (600), `/etc/monitorwibu/nodes.json` (600),
`/etc/monitorwibu/node.conf` (600). Keadaan runtime: `/var/lib/monitorwibu*`.

## 2. Perintah penting

```bash
# master
monitorwibu add-node SG-1     # buat kunci (64 hex) + cetak perintah pemasangan node
monitorwibu list              # node + umur laporan + uptime + status layanan
monitorwibu show              # dashboard ke layar (tidak dikirim)
monitorwibu render            # kirim/perbarui pesan Telegram
monitorwibu del-node SG-1
monitorwibu test              # uji tanda tangan sendiri
# node
monitorwibu-node              # kirim metrik sekarang
# uji
python3 tests/test_monitorwibu.py    # 26 uji (unit + HTTP sungguhan + interop openssl)
```

## 3. Protokol (kalau ada yang menambah klien)

```
POST /api/report            Content-Type: application/json
X-Wibu-Node: SG-1           nama terdaftar (A-Z a-z 0-9 _ -, maks 50)
X-Wibu-Ts:   1794836947     epoch detik, toleransi ±90 detik
X-Wibu-Sig:  <64 hex>       HMAC-SHA256(badan_persis_yang_dikirim, kunci_node)
Body: {"data": { ... }}     lihat monitorwibu.parse_data() untuk batas field
Jawaban: 200 OK · 400 bad payload/name · 403 unknown node/bad signature · 413 too large · 429 slow down
GET /health -> {"ok":true,"node":N,"time":...}
```
Contoh klien shell (tanda tangan identik dengan Python — sudah diuji):
```bash
body='{"data":{"uptime":"1 Jam"}}'
sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$KEY" -binary | od -An -tx1 | tr -d ' \n')
curl -sS -X POST "http://MASTER:5000/api/report" \
  -H "Content-Type: application/json" -H "X-Wibu-Node: SG-1" \
  -H "X-Wibu-Ts: $(date +%s)" -H "X-Wibu-Sig: $sig" --data "$body"
```

## 4. Yang WAJIB dijaga saat mengubah

1. **Jangan** mengganti HMAC dengan "kunci di header" — itu justru masalah v1 (kunci bisa disadap,
   satu kunci untuk semua node).
2. **Jangan** menambah dependensi luar (Flask/pip). Seluruh server memakai `http.server` stdlib;
   ini yang membuat pemasangan satu-langkah dan tanpa venv.
3. Nilai dari node **selalu** lewat `html.escape` sebelum masuk pesan Telegram (mode HTML).
4. `monitorwibu.NodeDB.save()` menulis 600 + `os.replace` — pertahankan (kunci adalah rahasia).
5. `store_report()` menulis atomik; nama berkas dari `sanitize_name()` (anti path traversal).
6. Batas laju ada di memori (`RateLimiter`); kalau master di-restart, batas ikut reset — untuk
   pemakaian sekarang tidak masalah, kalau nanti perlu lintas-restart, simpan ke berkas.

## 5. Uji yang harus lulus sebelum kirim

```bash
for f in install-master.sh install-node.sh uninstall.sh; do bash -n "$f"; done
python3 tests/test_monitorwibu.py          # 26 uji
# uji sungguhan (master + agen nyata, port lokal):
CONF=/tmp/m.conf ; printf 'BOT_TOKEN=""\nCHAT_ID=""\nMASTER_NAME="T"\nPORT="5055"\nBIND="127.0.0.1"\nSTATE_DIR="/tmp/st"\nNODES_DB="/tmp/n.json"\n' > $CONF
MONITORWIBU_CONF=$CONF python3 bin/monitorwibu add-node TES
KEY=$(python3 -c "import json;print(json.load(open('/tmp/n.json'))['TES'])")
MONITORWIBU_CONF=$CONF MONITORWIBU_STATE=/tmp/st python3 lib/api_server.py & sleep 1
printf 'MASTER="http://127.0.0.1:5055"\nNAME="TES"\nKEY="%s"\n' "$KEY" > /tmp/nc.conf
MONITORWIBU_NODE_CONF=/tmp/nc.conf python3 bin/monitorwibu-node   # harap: ✅ terkirim: HTTP 200
```
Hasil uji terakhir (2026-11-16): 26/26 uji lulus · E2E nyata: 200 untuk kunci benar,
403 `bad signature` untuk kunci salah, 403 `unknown node` untuk nama tak terdaftar,
30× 200 lalu 7× 429 saat melewati batas 30/menit, laporan basi dipangkas.

## 6. Belum dikerjakan (kalau mau lanjut)

1. **Alert proaktif**: sekarang peringatan hanya muncul di dashboard; belum ada pesan terpisah
   saat node mati > N menit (ide: `monitorwibu watch` + timer 5 menit).
2. **HTTPS bawaan**: sudah ada `TLS_CERT`/`TLS_KEY`, tapi belum ada pembantu certbot/self-signed.
3. **Riwayat & grafik**: hanya snapshot terakhir yang disimpan (tidak ada time-series).
4. **Kunci lintas-restart** untuk batas laju (lihat 4.6).
5. **Instalasi satu baris** (curl | bash) bila nanti dipublikasikan.

## 7. Keamanan & kerahasiaan

- Paket ini **tidak** memuat token/kunci: `master.conf`, `nodes.json`, `node.conf` dibuat saat
  pemasangan (mode 600). Jangan pernah menaruh kunci asli ke dalam repo/paket.
- Repo publik `WBVPN/monitorwibu` **sengaja tanpa sumber** (commit "keep binaries only").
  Simpan sumber ini di tempat privat (repo privat/arsip), **jangan** dorong ke repo publik itu.
