# 📝 Changelog — WIBU MONITOR

## v2.0 (2026-11-16) — perbaikan & perapian

Sumber v1 (`master.sh`, `node.sh`, `uninstall.sh`) dipulihkan dari riwayat git
`WBVPN/Wibu-Monitor` (commit `78cece0`, tepat sebelum commit `24a1c49` yang
mengeluarkan sumber dari repo). Fitur asli dipertahankan, ini yang diperbaiki:

**Keamanan**
- **Kunci per node** menggantikan satu `X-API-Key` untuk semua node → satu node bocor tidak
  bisa meniru node lain.
- **Kunci tidak lagi dikirim** ke jaringan: node menandatangani badan permintaan dengan
  **HMAC-SHA256**, master memverifikasi dengan `hmac.compare_digest`.
- **Jendela waktu 90 detik** pada tanda tangan → permintaan tidak bisa dipakai ulang (replay).
- **Port 5000 tidak dibuka otomatis lagi** (v1 melakukan `iptables -I INPUT --dport 5000 -j ACCEPT`
  dan `ufw allow 5000/tcp` ke seluruh internet); sekarang installer hanya menyarankan aturan
  per-IP node.
- **Escape HTML** pada seluruh nilai sebelum dikirim ke Telegram (v1 menyisipkan data node
  mentah ke pesan mode HTML → bisa disuntik markup).
- Batas ukuran badan permintaan, jumlah/panjang/tipe field, dan karakter kontrol divalidasi.
- Penolakan dicatat di log **beserta alasannya** (`unknown node` / `bad signature` / `ts-window`).

**Kerapian & operasional**
- **Nol dependensi**: server HTTP ditulis dengan `http.server` stdlib — v1 menuntut
  `python3-flask` + `flask-limiter` yang dipasang ad hoc dengan `pip3`.
- **systemd** (unit + timer) menggantikan `pkill`/`nohup` dan cron: auto-restart, log journald,
  bangkit otomatis setelah reboot.
- Server API dipisah menjadi berkas sendiri (`lib/api_server.py`) — v1 menulisnya lewat
  heredoc di tengah skrip.
- Konfigurasi terpusat: `/etc/wibu-monitor/master.conf` + `nodes.json` (600), keadaan di
  `/var/lib/wibu-monitor/` — v1 menyebar di `/root/.wibu_bot.conf`, `/root/node_*.txt`,
  `/root/api_server.py`, `/root/.wibu_msg_id`, `/root/.wibu_geo_cache*`.
- Pembersihan laporan basi tetap 300 detik, tapi ada `prune_reports()` yang teruji.
- `uninstall.sh` punya `--purge` yang sekaligus membuang sisa v1 (cron, berkas `/root/.wibu_*`).

**Fitur tambahan**
- `wibu-monitor list` — daftar node + umur laporan terakhir + uptime + status layanan.
- Metrik baru: **RAM**, **Disk**, dan **load average** (v1 hanya uptime/speed/traffic).
- **Peringatan otomatis**: RAM/disk ≥ 90% atau layanan mati → muncul di dashboard.
- Traffic tetap memakai `vnstat` bila ada, tapi ada **fallback `/proc/net/dev`** (hitung selisih)
  sehingga metrik jalan di VPS tanpa vnstat.
- Speed test jadi **opsional** (`SPEEDTEST=1`) — v1 menjalankannya setiap menit.
- Tambah node **tanpa restart master** (daftar kunci dimuat ulang saat berkasnya berubah).
- **26 uji otomatis** (`tests/test_whmon.py`), termasuk server HTTP sungguhan dan uji
  interoperabilitas tanda tangan dengan `openssl` (klien shell tetap bisa dipakai).

## v3.0 (2026-09-27) — versi awal (dari arsip repo)

- Autentikasi `X-API-Key` auto-generate menggantikan whitelist IP.
- Rate limit Flask-Limiter, sanitasi nama node, cache memori, tulis atomik.
- Dashboard Telegram: domain/IP disamarkan, uptime, speed test, traffic harian/bulanan.
