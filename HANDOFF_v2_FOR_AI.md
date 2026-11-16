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
Jawaban: 200 OK · 400 bad payload/name/panjang · 403 unknown node/bad signature · 404 salah path ·
          408 anggaran waktu habis · 413 terlalu besar · 429 batas laju · 503 registry rusak
GET /health -> {"ok":bool,"node":N,"registry":"ok|damaged","time":...}
(/health menyegarkan status registry sendiri; `ok` false saat registry rusak)
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
- Repo publik `WBVPN/monitorwibu` kini memuat sumber lengkap (tidak lagi biner saja).
  Rahasia tetap **tidak boleh** di-commit: `master.conf`, `nodes.json`, dan `node.conf`
  dibuat saat pemasangan (mode 600) dan tidak pernah masuk repo/paket — publik boleh,
  kunci tidak.

## 8. Riwayat review (untuk agen berikutnya)

- Putaran 1: 3 reviewer (konformansi, keamanan, perilaku) -> 19 temuan (12 major).
- Putaran 2: konformansi 19/19 CONFIRMED; falsifier menemukan 6 klaim tembus
  (termasuk command injection di installer dan RATE_IP pra-verifikasi).
- Putaran 3: konformansi 6/6 CONFIRMED + 1 klaim dokumen berlebihan; falsifier menemukan 5
  temuan pada jalur baru — desync 404/429 (sisa badan), `/health` berbagi kuota `RATE_IP`,
  `nodes.json` non-objek diterima senyap, `MASTER_NAME` tanpa validasi.
- Putaran 4: konformansi 5/5 CONFIRMED + 6 klaim dokumen tak persis; falsifier menemukan 5
  lagi — badan tanpa deadline total (slowloris badan, 28 detik), flag `damaged` basi,
  namespace `/health` memenuhi tabel `RATE_IP`, `Content-Length` ganda, deteksi mtime float.
- Putaran 5: konformansi 5/5 CONFIRMED + 2 suntingan orchestrator terverifikasi; falsifier
  menemukan 3 lagi — anggaran waktu belum mencakup fase header (slowloris header, 42 detik),
  `/health` tidak menyegarkan status registry (basi dua arah), dan `db_changed` membaca ulang
  seluruh `nodes.json` tiap laporan.
- Putaran 6: verifikasi sempit atas 3 perbaikan putaran 5 pada artefak beku.
- Semua temuan punya kasus uji di `tests/test_findings_red.py` (42 kasus uji, milik
  orchestrator; jangan diubah untuk membuat uji hijau) + `tests/test_monitorwibu.py` (26 uji).
- Harness temuan WAJIB hijau sebelum rilis: `python3 tests/test_findings_red.py`.

## 9. Risiko sisa yang diketahui (jangan diklaim sudah tertutup)

- **Jumlah thread tak dibatasi**: `ThreadingHTTPServer` stdlib membuat satu thread per koneksi
  dan tidak punya batas. Tiap siklus permintaan memakai **dua** thread sesaat (satu penangan + 
  satu jam pengawas anggaran waktu), jadi 1.000 koneksi lambat berarti ~2.000 thread selama
  ~10 detik (terukur: 100 koneksi slowloris -> +200 thread, kembali ke baseline setelah 11
  detik). Jangan buka port master ke internet: batasi lewat firewall ke IP node, atau taruh di
  belakang reverse proxy.
- **HTTP polos secara default**: node mengirim laporan tanpa TLS (tanda tangan HMAC tetap
  melindungi isi dari perubahan, bukan dari penyadapan metadata). TLS opsional lewat
  `TLS_CERT`/`TLS_KEY`; `--insecure`/`INSECURE=1` hanya mematikan **verifikasi sertifikat**
  (menerima self-signed), bukan TLS itu sendiri — jangan dipakai di jaringan terbuka.
- **`RATE_PRE` bukan proteksi CPU**: ia hanya menandai trafik tak terautentikasi dengan 429
  dan tidak menolak lebih awal. Permintaan sampah tetap diproses (satu HMAC, tanpa baca
  berkas). Manfaat sebenarnya: laporan bertanda tangan sah tidak bisa dibungkam hujan sampah.
- **Tabel ember bisa penuh (fail-closed)**: saat 4096 kunci berbeda muncul dalam satu jendela,
  kunci BARU ditolak (429) sampai ember lama kedaluwarsa. Ini batas memori yang disengaja;
  praktisnya butuh 4096 alamat sumber nyata dan hanya memengaruhi IP yang belum pernah melapor.
- **`node.conf` master memakai `http://127.0.0.1:$PORT`**: bila master dipasang dengan TLS,
  self-report-nya gagal sampai MASTER di `node.conf` disunting ke `https://`.
- **Berkas `nodes.json` non-objek** tidak lagi senyap, tetapi server tetap start dengan
  registry kosong (fail-closed: semua laporan 503) sampai berkas diperbaiki — `/health`
  melaporkan `registry: damaged`, jadi ini terlihat, bukan hilang.
- **Pra-saring `stat` bisa terlewat pada tabrakan yang dibuat-buat**: perubahan isi terdeteksi
  lewat `mtime_ns` + ukuran lebih dulu, baru sha256; bila keduanya dibuat sama persis (restore
  backup `cp -p` lalu kunci 64-hex ditukar), perubahan terlewat sampai berkas berubah lagi.
  Butuh akses tulis ke `nodes.json` — bukan jalur serang dari luar, tetapi jangan diandalkan
  sebagai pengaman terhadap admin/root.
