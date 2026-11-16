#!/bin/bash
# ==========================================================
# WIBU MONITOR v2 — pemasangan sisi MASTER
#   bash install-master.sh                 (interaktif)
#   bash install-master.sh --bot-token X --chat-id Y --name "ID-JKT-1" [--port 5000]
#                           [--bind 0.0.0.0] [--public-host vps.example.com] [--no-firewall]
# ==========================================================
set -euo pipefail

ETC=/etc/wibu-monitor
STATE=/var/lib/wibu-monitor
LIBDIR=/usr/local/lib/wibu-monitor
BIN=/usr/local/bin
UNIT=/etc/systemd/system
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BOT_TOKEN=""; CHAT_ID=""; MASTER_NAME=""; PORT="5000"; BIND="0.0.0.0"
PUBLIC_HOST=""; FIREWALL="tanya"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bot-token)   BOT_TOKEN="$2"; shift 2 ;;
        --chat-id)     CHAT_ID="$2"; shift 2 ;;
        --name)        MASTER_NAME="$2"; shift 2 ;;
        --port)        PORT="$2"; shift 2 ;;
        --bind)        BIND="$2"; shift 2 ;;
        --public-host) PUBLIC_HOST="$2"; shift 2 ;;
        --no-firewall) FIREWALL="tidak"; shift ;;
        -h|--help)     sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "  [!] argumen tak dikenal: $1"; exit 1 ;;
    esac
done

say() { printf '  %s\n' "$*"; }
die() { printf '  [!] %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "jalankan sebagai root"
command -v python3 >/dev/null || die "python3 tidak ada (apt install python3)"

say "🦊 WIBU MONITOR v2 — pemasangan master"
say "────────────────────────────────────────"

# ---------------------------------------------------------------- 1. konfigurasi
if [[ -f $ETC/master.conf && -z $BOT_TOKEN ]]; then
    say "[1] master.conf sudah ada — dilewati (hapus berkasnya untuk setup ulang)"
else
    if [[ -z $BOT_TOKEN ]]; then
        [[ -t 0 ]] || die "tanpa TTY, isi lewat --bot-token/--chat-id/--name"
        read -rp "  Bot Token  : " BOT_TOKEN
        read -rp "  Chat ID    : " CHAT_ID
        read -rp "  Nama Master: " MASTER_NAME
    fi
    [[ -n $BOT_TOKEN && -n $CHAT_ID && -n $MASTER_NAME ]] || die "bot token, chat id, dan nama wajib"
    [[ -z $PUBLIC_HOST ]] && PUBLIC_HOST="$(curl -4 -s --max-time 8 ifconfig.me || true)"
    install -d -m 700 "$ETC" "$STATE" "$STATE/nodes" "$LIBDIR"
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    {
        echo "# WIBU MONITOR v2 — konfigurasi master (mode 600)"
        echo "BOT_TOKEN=\"$BOT_TOKEN\""
        echo "CHAT_ID=\"$CHAT_ID\""
        echo "MASTER_NAME=\"$MASTER_NAME\""
        echo "PORT=\"$PORT\""
        echo "BIND=\"$BIND\""
        echo "PUBLIC_HOST=\"$PUBLIC_HOST\""
        echo "STATE_DIR=\"$STATE\""
        echo "NODES_DB=\"$ETC/nodes.json\""
        echo "FRESH=\"300\""
        echo "TZ=\"WIB\""
        echo "# TLS opsional: isi dua baris di bawah untuk HTTPS (mis. sertifikat certbot)"
        echo "#TLS_CERT=\"\""
        echo "#TLS_KEY=\"\""
    } > "$TMP"
    install -m 600 "$TMP" "$ETC/master.conf"
    say "[1] konfigurasi ditulis: $ETC/master.conf (600)"
fi

# ------------------------------------------------------------------- 2. berkas
install -m 755 "$SRC/bin/wibu-monitor"      "$BIN/wibu-monitor"
install -m 755 "$SRC/bin/wibu-monitor-node" "$BIN/wibu-monitor-node"
install -m 644 "$SRC/lib/whmon.py"          "$LIBDIR/whmon.py"
install -m 644 "$SRC/lib/api_server.py"     "$LIBDIR/api_server.py"
install -d -m 755 "$LIBDIR/tests" 2>/dev/null || true
say "[2] program dipasang: /usr/local/bin/wibu-monitor (+ node), lib di $LIBDIR"

# ------------------------------------------------------------------ 3. systemd
sed "s|__LIBDIR__|$LIBDIR|g" "$SRC/systemd/wibu-monitor-api.service"    > "$UNIT/wibu-monitor-api.service"
sed "s|__BIN__|$BIN|g"        "$SRC/systemd/wibu-monitor-render.service" > "$UNIT/wibu-monitor-render.service"
sed "s|__BIN__|$BIN|g"        "$SRC/systemd/wibu-monitor-node.service"   > "$UNIT/wibu-monitor-node.service"
install -m 644 "$SRC/systemd/wibu-monitor-render.timer" "$UNIT/wibu-monitor-render.timer"
install -m 644 "$SRC/systemd/wibu-monitor-node.timer"   "$UNIT/wibu-monitor-node.timer"
systemctl daemon-reload
systemctl enable --now wibu-monitor-api.service >/dev/null 2>&1 || true
systemctl enable --now wibu-monitor-render.timer >/dev/null 2>&1 || true
say "[3] layanan: wibu-monitor-api (aktif) + timer render tiap 60 detik"

# ----------------------------------------------------------------- 4. firewall
PORT_NOW=$(grep -oP '^PORT="\K[^"]+' "$ETC/master.conf" || echo "$PORT")
if [[ $FIREWALL == "tanya" ]]; then
    say "[4] port $PORT_NOW TIDAK dibuka otomatis (v1 membuka ke seluruh internet)."
    say "    Disarankan batasi per IP node:"
    say "      ufw allow from <IP-NODE> to any port $PORT_NOW proto tcp"
    say "      # atau: iptables -I INPUT -s <IP-NODE> -p tcp --dport $PORT_NOW -j ACCEPT"
    say "    Kalau memang mau terbuka untuk semua (kurang aman):"
    say "      ufw allow $PORT_NOW/tcp"
fi

# ------------------------------------------------------------------- 5. self-test
say "[5] uji cepat"
python3 - <<'PY'
import os, subprocess, sys
sys.path.insert(0, "/usr/local/lib/wibu-monitor")
import whmon
print("    modul whmon  : OK (hmac sendiri cocok:", whmon.verify("k", b"data", whmon.sign("k", b"data"), __import__("time").time())[0], ")")
PY
systemctl is-active --quiet wibu-monitor-api && say "    API server   : aktif" || say "    API server   : TIDAK aktif (cek: journalctl -u wibu-monitor-api)"
curl -4 -s --max-time 5 "http://127.0.0.1:${PORT_NOW}/health" && echo "    /health      : OK" || say "    /health      : tidak menjawab"

cat <<EOT

  ✅ Master siap dipakai.

  Tambah node:      wibu-monitor add-node SG-1
  Lihat node:       wibu-monitor list
  Lihat dashboard:  wibu-monitor show      (kirim/perbarui: wibu-monitor render)
  Log:              journalctl -u wibu-monitor-api -f

EOT
