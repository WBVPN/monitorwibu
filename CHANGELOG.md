# 📝 Changelog — MONITOR WIBU

## v2.0.1 (2026-11-16) — hasil 5 putaran review (38 temuan ditutup)

Lima putaran review independen memeriksa v2.0 (konformansi, falsifikasi keamanan, falsifikasi
perilaku+regresi). 19 temuan putaran 1, 6 putaran 2, 5 putaran 3, 5 putaran 4, dan 3 putaran 5
semuanya ditutup, dan setiap temuan punya kasus uji sendiri (`tests/test_findings_red.py`, 42
kasus uji — sebagian besar menjalankan perilaku sungguhan lewat HTTP/soket sungguhan, 7 di
antaranya memeriksa isi berkas pemasangan/konfigurasi).

**Keamanan**
- **Batas laju diperbaiki**: `RATE_IP` kini dikonsumsi **setelah** verifikasi tanda tangan
  (dulu sebelumnya → 60 kiriman sampah dari satu IP bisa membungkam node sah se-IP itu,
  termasuk node master sendiri yang melapor dari `127.0.0.1`). Pembatas pra-auth `RATE_PRE`
  (600/menit) hanya **menandai** trafik tak terautentikasi dengan 429: permintaan yang tanda
  tangannya sah tetap dilayani walau kuota itu habis, jadi hujan sampah tidak lagi bisa
  membungkam laporan node sah. Ongkos memproses satu permintaan sampah = satu HMAC (tanpa
  baca berkas) — jangan mengandalkan `RATE_PRE` sebagai proteksi CPU.
- **Evisi RateLimiter tidak lagi mereset ember aktif** (dulu batas bisa ditembus dengan
  mengganti-ganti kunci); bila penuh, kunci baru ditolak.
- **Handler punya timeout** (`timeout = 10`) → slowloris tidak lagi melahirkan ratusan thread
  menggantung.
- **Sisa badan tidak pernah diurai sebagai permintaan berikutnya**: balasan yang menutup koneksi
  (`Connection: close`) adalah 400 (panjang/header tidak sah), 404, 408, 413, dan 501. Balasan
  **429** tidak menyetelnya karena ia dikirim **setelah** badan dibaca dan dibuang, jadi tidak
  ada sisa badan yang perlu ditutup. Dulu sisa badan 404/429 menyatu dengan request berikutnya
  hingga server menjawab `501 Unsupported method ('HELLOGET')`.
- **Anggaran waktu 10 detik per siklus permintaan** mencakup request-line, header, **dan** badan:
  klien yang meneteskan header (1 byte per beberapa detik) maupun badan dilepas dengan **408**
  atau koneksi ditutup pada ~10 detik. `Handler.timeout` saja tidak cukup — ia hanya membatasi
  jeda antar-baca, jadi satu koneksi bisa menahan satu thread tanpa batas (terbukti: badan 28
  detik, header 42 detik, dan secara teori berhari-hari untuk badan 96 KiB).
- **Header `Content-Length` ganda atau `Transfer-Encoding` ditolak 400** sebelum badan dibaca
  (dulu server memakai nilai pertama sehingga sisa badan menyatu dengan request berikutnya).
- **`nodes.json` rusak** → **503** + pesan, server tetap hidup, pulih tanpa restart sesudah
  berkas diperbaiki, dan daftar kunci lama tidak diganti. Pesan ke **stderr** berlaku untuk
  kerusakan saat start; kerusakan saat berjalan dilaporkan lewat log server (`self.log_message`,
  stdout) dan terlihat di `/health` sebagai `registry: damaged`.
- **Status registry tidak basi**: rusaknya `nodes.json` saat server berjalan kini langsung
  menandai registry rusak, jadi `/health` **tidak lagi mengaku `ok`** sementara seluruh laporan
  diblok 503; setelah berkas diperbaiki, status kembali `ok`.
- **Perubahan `nodes.json` dideteksi dari isi** (sha256), bukan dari mtime float yang beresolusi
  ~4 ms — tulis-ulang cepat dengan mtime identik dulu membuat server tetap memakai daftar kunci
  lama (laporan tetap 200 pada berkas yang sudah rusak). Pemeriksaan itu **murah**: `stat`
  (mtime_ns + ukuran) diperiksa lebih dulu dan isi hanya dibaca+di-hash bila salah satunya
  berubah — dulu setiap laporan membaca ulang seluruh berkas (~0,93 ms pada `nodes.json`
  1,16 MB, tumbuh linier dengan jumlah node, dan bisa dipicu tanpa kunci). Konsekuensi jujur
  dari pra-saring itu: bila `mtime_ns` **dan** ukuran byte dibuat sama persis padahal isinya
  berbeda (mis. `cp -p`/restore backup lalu kunci 64-hex ditukar), perubahan itu terlewat sampai
  berkas berubah lagi. Ini butuh akses tulis ke `nodes.json` — bukan jalur penyerang dari luar.
- **Badan 403 diseragamkan** (`forbidden`) → tidak lagi menjadi oracle untuk menebak nama node
  terdaftar; alasan rinci hanya di log.
- **`/health` dibatasi laju** dan memakai `fresh=300` (dulu `10**9` → memindai semua berkas);
  kini **punya pembatas sendiri (`RATE_HEALTH`)**, benar-benar terpisah dari kuota laporan node
  sah: dulu keduanya berbagi objek `RATE_IP` sehingga 60 `GET /health`/menit dari satu IP
  membungkam laporan node sah se-IP itu, dan kunci namespace `/health` bahkan bisa memenuhi
  tabel ember (`MAX_BUCKETS`) sehingga laporan dari IP yang belum pernah melapor ditolak 429.
  `/health` juga **menyegarkan status registry sendiri** (memanggil pemeriksa yang sama seperti
  jalur laporan), jadi statusnya tidak lagi basi: dulu `/health` bisa melaporkan `ok` padahal
  `nodes.json` sudah rusak (belum ada laporan masuk), dan tetap `damaged` setelah berkas
  dipulihkan sampai ada laporan masuk. `/health` juga **melaporkan status registry** (`registry: ok|damaged`):
  `nodes.json` yang sah tapi bukan objek (`null`/`[]`) tidak lagi diterima senyap — ada peringatan
  ke stderr dan `/health` tidak lagi mengaku `ok`.
- **`del-node` divalidasi** (dulu `del-node ../../canary` bisa menghapus berkas di luar direktori
  laporan).
- **Installer bebas injeksi**: `MASTER_NAME` divalidasi (pola `NAME_RE`: `^[A-Za-z0-9_-]{1,50}$`)
  sebelum ditulis ke `node.conf` dan dilewatkan lewat `argv` (tidak lagi disisipkan ke string
  `python3 -c`); `node.conf` tidak ditulis dan timer node tidak dinyalakan bila kunci kosong.
- **Badan JSON non-objek** (list/str/angka) dibalas 400, bukan memutus koneksi.

**Ketahanan**
- Satu berkas laporan rusak (`data` bukan objek) tidak lagi mematikan dashboard **semua** node.
- Field `services` yang bukan daftar ditolak di pintu masuk (`parse_data`) dan tidak lagi memicu
  `TypeError` di render/`list`.
- Batas kesegaran 300 detik konsisten antara baca dan pangkas.

**Fitur & regresi**
- **Master memonitor dirinya sendiri** (regresi v1): installer membuat `/etc/monitorwibu/node.conf`
  untuk master + mengaktifkan `monitorwibu-node.timer`.
- **Pesan Telegram yang terhapus pulih otomatis**: `tg()` membaca badan galat Telegram sehingga
  cabang "message to edit not found" benar-benar tercapai dan render mengirim pesan baru
  (dulu gagal selamanya — regresi dari v1).
- Baris **Load** ditampilkan (sebelumnya dikumpulkan tapi tak pernah muncul).
- Traffic memakai **total** vnstat (`GiB`), bukan laju `kbit/s`.
- `mask_ip` menyamarkan **IPv6** juga (dulu alamat IPv6 tampil utuh).

## v2.0 (2026-11-16) — versi publik pertama

Sumber v1 (`master.sh`, `node.sh`, `uninstall.sh`) dipulihkan dari riwayat git
`WBVPN/monitorwibu` (commit `78cece0`, tepat sebelum commit `24a1c49` yang
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
- Konfigurasi terpusat: `/etc/monitorwibu/master.conf` + `nodes.json` (600), keadaan di
  `/var/lib/monitorwibu/` — v1 menyebar di `/root/.wibu_bot.conf`, `/root/node_*.txt`,
  `/root/api_server.py`, `/root/.wibu_msg_id`, `/root/.wibu_geo_cache*`.
- Pembersihan laporan basi tetap 300 detik, tapi ada `prune_reports()` yang teruji.
- `uninstall.sh` punya `--purge` yang sekaligus membuang sisa v1 (cron, berkas `/root/.wibu_*`).

**Fitur tambahan**
- `monitorwibu list` — daftar node + umur laporan terakhir + uptime + status layanan.
- Metrik baru: **RAM**, **Disk**, dan **load average** (v1 hanya uptime/speed/traffic).
- **Peringatan otomatis**: RAM/disk ≥ 90% atau layanan mati → muncul di dashboard.
- Traffic tetap memakai `vnstat` bila ada, tapi ada **fallback `/proc/net/dev`** (hitung selisih)
  sehingga metrik jalan di VPS tanpa vnstat.
- Speed test jadi **opsional** (`SPEEDTEST=1`) — v1 menjalankannya setiap menit.
- Tambah node **tanpa restart master** (daftar kunci dimuat ulang saat berkasnya berubah).
- **26 uji otomatis** (`tests/test_monitorwibu.py`), termasuk server HTTP sungguhan dan uji
  interoperabilitas tanda tangan dengan `openssl` (klien shell tetap bisa dipakai).

## v3.0 (2026-09-27) — versi awal (dari arsip repo)

- Autentikasi `X-API-Key` auto-generate menggantikan whitelist IP.
- Rate limit Flask-Limiter, sanitasi nama node, cache memori, tulis atomik.
- Dashboard Telegram: domain/IP disamarkan, uptime, speed test, traffic harian/bulanan.
