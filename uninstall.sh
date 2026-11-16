#!/bin/bash
# ==========================================================
# MONITOR WIBU v2 — penghapusan
#   bash uninstall.sh --master         hapus sisi master (layanan, timer, konfig, data)
#   bash uninstall.sh --node           hapus sisi node
#   bash uninstall.sh --master --node  hapus keduanya
#   bash uninstall.sh --all --purge    hapus + buang juga kunci/konfigurasi & sisa v1
# ==========================================================
set -uo pipefail

ETC=/etc/monitorwibu
LIBDIR=/usr/local/lib/monitorwibu
BIN=/usr/local/bin
UNIT=/etc/systemd/system
MASTER=0; NODE=0; PURGE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --master) MASTER=1; shift ;;
        --node)   NODE=1; shift ;;
        --all)    MASTER=1; NODE=1; shift ;;
        --purge)  PURGE=1; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "  [!] argumen tak dikenal: $1"; exit 1 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "  [!] jalankan sebagai root"; exit 1; }
[[ $((MASTER + NODE)) -gt 0 ]] || { sed -n '2,9p' "$0"; exit 1; }

say() { printf '  %s\n' "$*"; }
stop_unit() {
    for u in "$@"; do
        if systemctl list-unit-files 2>/dev/null | grep -q "^$u"; then
            systemctl disable --now "$u" >/dev/null 2>&1
            rm -f "$UNIT/$u"
            say "dihapus: $u"
        fi
    done
}

if [[ $MASTER -eq 1 ]]; then
    say "── sisi master ──"
    stop_unit monitorwibu-api.service monitorwibu-render.timer monitorwibu-render.service
    rm -f "$BIN/monitorwibu"
    if [[ $PURGE -eq 1 ]]; then
        rm -rf "$ETC" "$LIBDIR" /var/lib/monitorwibu
        say "purge: konfigurasi, kunci, dan data dashboard dibuang"
    else
        rm -rf /var/lib/monitorwibu
        say "catatan: $ETC (berisi token & kunci node) DIBIARKAN — pakai --purge untuk membuang"
    fi
fi

if [[ $NODE -eq 1 ]]; then
    say "── sisi node ──"
    stop_unit monitorwibu-node.timer monitorwibu-node.service
    rm -f "$BIN/monitorwibu-node"
    rm -rf /var/lib/monitorwibu-node
    [[ $PURGE -eq 1 ]] && rm -f "$ETC/node.conf" && say "purge: node.conf (berisi kunci) dibuang"
fi

systemctl daemon-reload 2>/dev/null
say "daemon-reload selesai"

# ── sisa versi lama (v1: master.sh/node.sh) supaya benar-benar bersih ──
if [[ $PURGE -eq 1 ]]; then
    say "── membersihkan sisa v1 ──"
    for f in /root/.wibu_bot.conf /root/.wibu_node.conf /root/.wibu_msg_id /root/.wibu_geo_cache \
             /root/.wibu_geo_cache_node /root/api_server.py /root/wibu_master.sh /root/wibu_node.sh; do
        [[ -e $f ]] && { rm -f "$f"; say "dihapus: $f"; }
    done
    rm -f /root/node_*.txt 2>/dev/null
    if crontab -l 2>/dev/null | grep -qE 'wibu_(master|node)\.sh'; then
        crontab -l 2>/dev/null | grep -vE 'wibu_(master|node)\.sh' | crontab -
        say "entri cron v1 dibuang"
    fi
    if systemctl list-units --all 2>/dev/null | grep -q 'monitorwibu-api'; then
        systemctl reset-failed monitorwibu-api monitorwibu-render monitorwibu-node 2>/dev/null
    fi
fi

say "✅ selesai"
