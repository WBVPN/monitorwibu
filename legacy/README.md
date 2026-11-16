# legacy/ — peninggalan versi 1

`v1-binaries/` berisi tiga program yang dulu dipublikasikan sebagai satu-satunya distribusi
(WIBU MONITOR v3.0, 2026-09-27). Ketiganya skrip shell yang dikompilasi dengan **shc** (ARC4)
dan **sudah tidak bisa dijalankan lagi**:

- tertanam tanggal kedaluwarsa (build 27 Sep 2026) → `wibu_uninstall` menolak jalan
  *"has expired! Please contact your provider ..."*;
- terikat lokasi berkas → setelah dipindah/diekstrak, dekripsi batal (*"location has changed!"*).

Sumber aslinya kini ada di repo ini (`install-master.sh`, `bin/`, `lib/`) — berkas di sini
hanya jejak sejarah. **Jangan dipakai.**
