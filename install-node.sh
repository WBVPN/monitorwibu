#!/bin/bash
# ==========================================================
# WIBU MONITOR v2 — pemasangan sisi NODE
#   bash install-node.sh --master http://1.2.3.4:5000 --name SG-1 --key <64hex>
#                        [--services xray,haproxy,dropbear,ws-stunnel]
#                        [--domain vps.example.com] [--insecure] [--speedtest]
# Kunci didapat dari master:  wibu-monitor add-node SG-1
# ==========================================================
set -euo pipefail

ETC=/etc/wibu-monitor
STATE=/var/lib/wibu-monitor-node
LIBDIR=/usr/local/lib/wibu-monitor
BIN=/usr/local/bin
UNIT=/etc/systemd/system
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MASTER=""; NAME=""; KEY=""; SERVICES="xray,haproxy,dropbear,ws-stunnel"
DOMAIN=""; INSECURE="0"; SPEEDTEST="0"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --master)    MASTER="$2"; shift 2 ;;
        --name)      NAME="$2"; shift 2 ;;
        --key)       KEY="$2"; shift 2 ;;
        --services)  SERVICES="$2"; shift 2 ;;
        --domain)    DOMAIN="$2"; shift 2 ;;
        --insecure)  INSECURE="1"; shift ;;
        --speedtest) SPEEDTEST="1"; shift ;;
        -h|--help)   sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "  [!] argumen tak dikenal: $1"; exit 1 ;;
    esac
done

say() { printf '  %s\n' "$*"; }
die() { printf '  [!] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "jalankan sebagai root"
command -v python3 >/dev/null || die "python3 tidak ada (apt install python3)"
[[ -n $MASTER && -n $NAME && -n $KEY ]] || die "wajib: --master, --name, --key (lihat wibu-monitor add-node di master)"
[[ $KEY =~ ^[0-9a-f]{64}$ ]] || die "format kunci harus 64 karakter hex"
[[ $NAME =~ ^[A-Za-z0-9_-]{1,50}$ ]] || die "nama node hanya A-Z a-z 0-9 _ - (maks 50)"

say "🦊 WIBU MONITOR v2 — pemasangan node '$NAME'"
say "────────────────────────────────────────"

# ---------------------------------------------------------------- 1. berkas
install -d -m 755 "$LIBDIR" "$ETC"
install -m 755 "$SRC/bin/wibu-monitor-node" "$BIN/wibu-monitor-node"
install -m 644 "$SRC/lib/whmon.py"          "$LIBDIR/whmon.py"
install -d -m 700 "$STATE"
say "[1] program dipasang di $BIN/wibu-monitor-node"

# ------------------------------------------------------------- 2. konfigurasi
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
{
    echo "# WIBU MONITOR v2 — konfigurasi node (mode 600)"
    echo "MASTER=\"$MASTER\""
    echo "NAME=\"$NAME\""
    echo "KEY=\"$KEY\""
    echo "SERVICES=\"$SERVICES\""
    echo "DOMAIN=\"$DOMAIN\""
    echo "INSECURE=\"$INSECURE\""
    echo "SPEEDTEST=\"$SPEEDTEST\""
} > "$TMP"
install -m 600 "$TMP" "$ETC/node.conf"
say "[2] konfigurasi: $ETC/node.conf (600)"

# ------------------------------------------------------------------ 3. systemd
sed "s|__BIN__|$BIN|g" "$SRC/systemd/wibu-monitor-node.service" > "$UNIT/wibu-monitor-node.service"
install -m 644 "$SRC/systemd/wibu-monitor-node.timer" "$UNIT/wibu-monitor-node.timer"
systemctl daemon-reload
systemctl enable --now wibu-monitor-node.timer >/dev/null 2>&1 || true
say "[3] timer node aktif (kirim metrik tiap 60 detik)"

# ------------------------------------------------------------------ 4. self-test
say "[4] uji kirim laporan"
if "$BIN/wibu-monitor-node"; then
    say "    ✅ master menerima laporan pertama"
else
    say "    ✗ gagal — cek MASTER ($MASTER), port terbuka di master, dan kunci benar"
    say "      master: curl -s http://<MASTER-IP>:<PORT>/health"
    exit 1
fi

cat <<EOT

  ✅ Node '$NAME' aktif.

  Kirim manual :   wibu-monitor-node
  Log          :   journalctl -u wibu-monitor-node -n 20
  Hapus        :   bash uninstall.sh --node

EOT
