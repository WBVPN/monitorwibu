# HANDOFF WIBU MONITOR v2 — UNTUK AI / AGEN BERIKUTNYA

Tanggal : 2026-11-16
Paket   : WIBU MONITOR v2 (perbaikan dari v3.0 / 2026-09-27)
Asal    : sumber v1 dipulihkan dari riwayat git `WBVPN/Wibu-Monitor` (commit `78cece0`,
          tepat sebelum `24a1c49` yang mengeluarkan sumber dari repo; repo pernah dihapus
          pemilik lalu di-restore dari GitHub "Deleted repositories" → 48 commit utuh)
Bahasa  : shell (instalasi) + Python 3 stdlib (server, agen, CLI) — **tanpa pip/Flask**

---

## 1. Arsitektur ringkas

```
VPS node (tiap menit, systemd timer wibu-monitor-node.timer)
   bin/wibu-monitor-node  ->  kumpulkan metrik  ->  POST /api/report
      header X-Wibu-Node / X-Wibu-Ts / X-Wibu-Sig (HMAC-SHA256 atas badan JSON)
VPS master
   lib/api_server.py  -> verifikasi kunci per node + jendela 90s + batas laju
                      -> simpan /var/lib/wibu-monitor/nodes/<NAMA>.json (atomik)
   bin/wibu-monitor render  -> susun pesan -> sendMessage / editMessageText
      (id pesan disimpan di /var/lib/wibu-monitor/message_id → satu pesan terus diperbarui)
```

Konfigurasi: `/etc/wibu-monitor/master.conf` (600), `/etc/wibu-monitor/nodes.json` (600),
`/etc/wibu-monitor/node.conf` (600). Keadaan runtime: `/var/lib/wibu-monitor*`.

## 2. Perintah penting

```bash
# master
wibu-monitor add-node SG-1     # buat kunci (64 hex) + cetak perintah pemasangan node
wibu-monitor list              # node + umur laporan + uptime + status layanan
wibu-monitor show              # dashboard ke layar (tidak dikirim)
wibu-monitor render            # kirim/perbarui pesan Telegram
wibu-monitor del-node SG-1
wibu-monitor test              # uji tanda tangan sendiri
# node
wibu-monitor-node              # kirim metrik sekarang
# uji
python3 tests/test_whmon.py    # 26 uji (unit + HTTP sungguhan + interop openssl)
```

## 3. Protokol (kalau ada yang menambah klien)

```
POST /api/report            Content-Type: application/json
X-Wibu-Node: SG-1           nama terdaftar (A-Z a-z 0-9 _ -, maks 50)
X-Wibu-Ts:   1794836947     epoch detik, toleransi ±90 detik
X-Wibu-Sig:  <64 hex>       HMAC-SHA256(badan_persis_yang_dikirim, kunci_node)
Body: {"data": { ... }}     lihat whmon.parse_data() untuk batas field
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
4. `whmon.NodeDB.save()` menulis 600 + `os.replace` — pertahankan (kunci adalah rahasia).
5. `store_report()` menulis atomik; nama berkas dari `sanitize_name()` (anti path traversal).
6. Batas laju ada di memori (`RateLimiter`); kalau master di-restart, batas ikut reset — untuk
   pemakaian sekarang tidak masalah, kalau nanti perlu lintas-restart, simpan ke berkas.

## 5. Uji yang harus lulus sebelum kirim

```bash
for f in install-master.sh install-node.sh uninstall.sh; do bash -n "$f"; done
python3 tests/test_whmon.py          # 26 uji
# uji sungguhan (master + agen nyata, port lokal):
CONF=/tmp/m.conf ; printf 'BOT_TOKEN=""\nCHAT_ID=""\nMASTER_NAME="T"\nPORT="5055"\nBIND="127.0.0.1"\nSTATE_DIR="/tmp/st"\nNODES_DB="/tmp/n.json"\n' > $CONF
WIBU_CONF=$CONF python3 bin/wibu-monitor add-node TES
KEY=$(python3 -c "import json;print(json.load(open('/tmp/n.json'))['TES'])")
WIBU_CONF=$CONF WIBU_STATE=/tmp/st python3 lib/api_server.py & sleep 1
printf 'MASTER="http://127.0.0.1:5055"\nNAME="TES"\nKEY="%s"\n' "$KEY" > /tmp/nc.conf
WIBU_NODE_CONF=/tmp/nc.conf python3 bin/wibu-monitor-node   # harap: ✅ terkirim: HTTP 200
```
Hasil uji terakhir (2026-11-16): 26/26 uji lulus · E2E nyata: 200 untuk kunci benar,
403 `bad signature` untuk kunci salah, 403 `unknown node` untuk nama tak terdaftar,
30× 200 lalu 7× 429 saat melewati batas 30/menit, laporan basi dipangkas.

## 6. Belum dikerjakan (kalau mau lanjut)

1. **Alert proaktif**: sekarang peringatan hanya muncul di dashboard; belum ada pesan terpisah
   saat node mati > N menit (ide: `wibu-monitor watch` + timer 5 menit).
2. **HTTPS bawaan**: sudah ada `TLS_CERT`/`TLS_KEY`, tapi belum ada pembantu certbot/self-signed.
3. **Riwayat & grafik**: hanya snapshot terakhir yang disimpan (tidak ada time-series).
4. **Kunci lintas-restart** untuk batas laju (lihat 4.6).
5. **Instalasi satu baris** (curl | bash) bila nanti dipublikasikan.

## 7. Keamanan & kerahasiaan

- Paket ini **tidak** memuat token/kunci: `master.conf`, `nodes.json`, `node.conf` dibuat saat
  pemasangan (mode 600). Jangan pernah menaruh kunci asli ke dalam repo/paket.
- Repo ini **publik** atas keputusan pemilik (2026-11-16). Biner v1 yang kedaluwarsa ada di
  `legacy/`; sumber asli v1 tetap bisa dibaca dari riwayat git (commit `78cece0`).
- **Jangan pernah** meng-commit `master.conf`, `nodes.json`, `node.conf`, token bot, atau kunci
  apa pun. Semuanya dibuat saat pemasangan dan ber-mode 600.
