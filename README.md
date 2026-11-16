# 🦊 MONITOR WIBU v2

Monitor multi-VPS real-time: tiap node mengirim metrik ke satu master, master menampilkan
dashboard di **satu pesan Telegram** yang diperbarui otomatis tiap 60 detik.

Versi ini adalah **perbaikan dari MONITOR WIBU v3.0 (2026-09-27)** yang sumbernya dipulihkan
dari riwayat git repo `WBVPN/monitorwibu`. Fitur aslinya dipertahankan; keamanan,
kerapian, dan cara operasinya diperbaiki.

## ✨ Fitur

| | |
|---|---|
| 🧠 **Metrik lengkap** | Uptime · RAM (%) · Disk (%) · Traffic hari ini/bulan · status layanan (xray/haproxy/dropbear/dll) |
| 🚀 **Speed test opsional** | `SPEEDTEST=1` → ikut `vnstat -tr 1` |
| 🏙️ **Lokasi otomatis** | cache 24 jam (ip-api), tidak menembak tiap menit |
| 🖥️ **Multi-VPS tanpa batas** | tiap node punya kunci sendiri; tambah node kapan saja tanpa restart master |
| 🔐 **Tanda tangan HMAC-SHA256** | kunci **tidak pernah** dikirim ke jaringan, jendela waktu 90 detik (anti-ulang) |
| ⚠️ **Peringatan otomatis** | RAM/disk ≥ 90% atau layanan mati → muncul di dashboard |
| 🧹 **Pembersihan sendiri** | laporan node lebih tua dari 300 detik dihapus (5 menit) |
| 📦 **Stdlib saja** | **tanpa** Flask/pip — cukup `python3` (v1 butuh Flask + Flask-Limiter) |
| ⚙️ **systemd** | unit + timer, auto-restart, log di journald (v1: `pkill`/`nohup` + cron) |

## 🚀 Pemasangan

**1) Di VPS master**
```bash
bash install-master.sh
# pertanyaan: Bot Token · Chat ID · Nama Master
# lalu tambahkan node satu per satu:
monitorwibu add-node SG-1      # mencetak perintah pemasangan untuk node itu
```
Bot token dari [@BotFather](https://t.me/BotFather), chat ID dari [@userinfobot](https://t.me/userinfobot).
Kalau tanpa TTY:
```bash
bash install-master.sh --bot-token <TOKEN> --chat-id <ID> --name "ID-JKT-1"
```

**2) Di setiap VPS node**
```bash
bash install-node.sh --master http://<IP-MASTER>:5000 --name SG-1 --key <kunci dari add-node>
# tambahan: --services xray,haproxy,dropbear,ws-stunnel   --domain vps.contoh.com
#           --speedtest    (ikut uji kecepatan)            --insecure (TLS self-signed)
```

**3) Perintah harian (di master)**
```bash
monitorwibu list          # node + umur laporan terakhir
monitorwibu show          # lihat dashboard di layar (tidak dikirim)
monitorwibu render        # kirim/perbarui pesan Telegram sekarang
monitorwibu add-node N    # tambah node
monitorwibu del-node N    # hapus node (kunci langsung tak berlaku)
monitorwibu test          # uji tanda tangan sendiri
```
Hapus: `bash uninstall.sh --all --purge` (sekalian membersihkan sisa v1).

## 🔐 Keamanan (yang berubah dari v1)

| v1 (2026-09-27) | v2 |
|---|---|
| Satu `X-API-Key` untuk **semua** node | **Kunci per node** (`nodes.json`, mode 600) |
| Kunci dikirim di header setiap permintaan | Kunci **tidak pernah** dikirim; yang dikirim tanda tangan HMAC-SHA256 atas badan permintaan |
| Bisa diulang (replay) | Jendela waktu **90 detik** |
| Port 5000 dibuka otomatis ke seluruh internet | **Tidak** dibuka otomatis; disarankan batasi per IP node |
| `python3-flask` + `flask-limiter` (pip) | **Stdlib saja** — nol dependensi |
| `pkill`/`nohup` + cron | unit + timer **systemd** (auto-restart, log journald) |
| Data node dirender ke HTML Telegram apa adanya | Semua nilai di-**escape** (`html.escape`) → tak bisa menyuntik markup |
| Nama node divalidasi 50 karakter | sama, + validasi field (jumlah, panjang, tipe, karakter kontrol) |

Catatan: transport masih boleh HTTP di jaringan pribadi antar VPS. Untuk internet terbuka,
pasang TLS: isi `TLS_CERT`/`TLS_KEY` di `/etc/monitorwibu/master.conf` (mis. dari certbot) —
server otomatis mendengarkan HTTPS.

## 🩺 Operasional

```bash
systemctl status monitorwibu-api        # penerima laporan
systemctl list-timers | grep wibu        # jadwal render & kirim node
journalctl -u monitorwibu-api -f        # log penerimaan + penolakan (alasan dicatat)
monitorwibu-node                        # kirim metrik manual (di node)
```
| Gejala | Cek |
|---|---|
| Node tidak muncul di dashboard | `journalctl -u monitorwibu-api -f` → `unknown node` (kunci/nama salah) · `bad signature` (kunci beda) · `ts-window` (jam node tidak sinkron) |
| Master tidak menerima | `curl -s http://<IP-MASTER>:5000/health` dari node · firewall/port |
| Pesan Telegram tidak berubah | `monitorwibu render` (pesan lama terhapus? CLI otomatis mengirim baru) |
| Dashboard "Belum ada node" | node belum kirim / laporan basi (>300 detik dianggap hilang) |

## 🧪 Uji

```bash
python3 tests/test_monitorwibu.py     # 26 uji: nama, batas data, HMAC (terima/tolak/basi/format),
                                # pembatas laju, escape HTML, penyimpanan, interop openssl,
                                # dan server HTTP sungguhan (200/403/400/413/429)
```

## 📁 Struktur

```
install-master.sh      pemasangan sisi master (+ systemd, konfigurasi, uji cepat)
install-node.sh        pemasangan sisi node
uninstall.sh           penghapusan (--master/--node/--all, --purge sekalian sisa v1)
bin/monitorwibu       CLI master (add-node/del-node/list/render/show/test)
bin/monitorwibu-node  agen node (kumpulkan metrik + kirim bertanda tangan)
lib/monitorwibu.py           inti logika (tanpa dependensi, bisa diuji langsung)
lib/api_server.py      penerima laporan (stdlib, TLS opsional, batas laju)
systemd/*.service|timer
tests/test_monitorwibu.py
```

Lisensi MIT — bebas dipakai. Dukungan: [@wibuvpn](https://t.me/wibuvpn)
