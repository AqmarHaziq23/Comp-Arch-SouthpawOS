#!/usr/bin/env bash
# backup.sh - SouthpawOS: back up (or restore) the Pi's settings and data that are NOT in git.
#
#   bash pi/backup.sh                       make ~/southpaw-backup-<date>-<time>.tar.gz
#   bash pi/backup.sh --restore <file>      put everything from a backup back, and restart the services
#
# What's in a backup (everything else can be rebuilt from git + the READMEs):
#   telegram_bot/.env          the bot token + ADMIN_CHAT_IDS            (SECRET)
#   telegram_bot/players.db    which card belongs to which Telegram chat, round history
#   pi/southpaw.env            game settings + the website token         (SECRET)
#   hub/southpaw.db            scores, sessions, leaderboard
#   hub/boards.toml            the 3 board addresses
#   hub/logs/unsent_rounds.jsonl   rounds still waiting for the website (only if there are any)
#   wifi/*.nmconnection        the saved Wi-Fi networks incl. their passwords (SECRET; needs sudo)
#
# The backup holds secrets, so it's only readable by this account, and it's saved in the home
# folder, OUTSIDE the repo folder, so it can never be committed by accident.
# Copy it to a computer with the scp command printed at the end, and keep it private.
#
# Databases are copied with SQLite's own backup function, so the copy is complete even while
# the hub and bot are running.

set -u
umask 077                                    # every file this script makes: only this account can read it

PI_DIR="$(cd "$(dirname "$0")" && pwd)"      # .../pi
DIR="$(dirname "$PI_DIR")"                   # the repo folder
PY="${PYTHON:-python3}"
WIFI_DIR="/etc/NetworkManager/system-connections"
FILES="telegram_bot/.env telegram_bot/players.db pi/southpaw.env hub/southpaw.db hub/boards.toml hub/logs/unsent_rounds.jsonl"

# Copy one SQLite database safely (works while a program has it open)
copy_db() {
    "$PY" - "$1" "$2" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)          # SQLite's own "make a complete copy" function
dst.close(); src.close()
EOF
}

# ------------------------------------------------------------------------------------------
if [ "${1:-}" = "--restore" ]; then
    BACKUP="${2:-}"
    if [ ! -f "$BACKUP" ]; then
        echo "Give the backup file:  bash pi/backup.sh --restore ~/southpaw-backup-....tar.gz"
        exit 1
    fi
    WORK="$(mktemp -d)"
    tar -xzf "$BACKUP" -C "$WORK" || { echo "Couldn't open $BACKUP"; rm -rf "$WORK"; exit 1; }
    echo "Stopping the SouthpawOS services while the files are put back..."
    sudo systemctl stop 'southpaw-*' 2>/dev/null
    for f in $FILES; do
        if [ -f "$WORK/$f" ]; then
            mkdir -p "$DIR/$(dirname "$f")"
            cp "$WORK/$f" "$DIR/$f" && echo "  restored $f"
        fi
    done
    if ls "$WORK"/wifi/*.nmconnection > /dev/null 2>&1; then
        # NetworkManager only accepts these files if root owns them and only root can read them
        sudo install -m 600 -o root -g root "$WORK"/wifi/*.nmconnection "$WIFI_DIR"/ \
            && sudo nmcli connection reload && echo "  restored the saved Wi-Fi networks"
    fi
    rm -rf "$WORK"
    sudo systemctl start 'southpaw-*' 2>/dev/null
    echo "Done. Check with:  systemctl status 'southpaw-*' --no-pager"
    exit 0
fi

# ------------------------------------------------------------------------------------------
STAMP="$(date +%Y%m%d-%H%M)"
OUT="$HOME/southpaw-backup-$STAMP.tar.gz"
WORK="$(mktemp -d)"

echo "Backing up from $DIR"
for f in $FILES; do
    [ -f "$DIR/$f" ] || continue                      # not there (e.g. no unsent rounds): skip it
    mkdir -p "$WORK/$(dirname "$f")"
    case "$f" in
        *.db) copy_db "$DIR/$f" "$WORK/$f" && echo "  + $f" || echo "  ! couldn't copy $f" ;;
        *)    cp "$DIR/$f" "$WORK/$f" && echo "  + $f" ;;
    esac
done

# The saved Wi-Fi networks: only root can read them, so this asks for your password once.
# If sudo isn't possible, the rest of the backup still works.
if sudo -v 2>/dev/null; then
    mkdir -p "$WORK/wifi"
    if sudo sh -c "cp $WIFI_DIR/*.nmconnection '$WORK/wifi/'" 2>/dev/null; then
        sudo chown -R "$(id -u):$(id -g)" "$WORK/wifi"
        echo "  + wifi: $(ls "$WORK/wifi" | wc -l) saved network(s)"
    else
        echo "  ! couldn't copy the Wi-Fi networks (skipped)"
    fi
fi

tar -czf "$OUT" -C "$WORK" . && rm -rf "$WORK"
chmod 600 "$OUT"
echo
echo "Saved: $OUT ($(du -h "$OUT" | cut -f1))"
echo "It holds the bot token, the website token and Wi-Fi passwords: keep it private."
echo
echo "Copy it to your computer (PowerShell, in the folder you want it in):"
echo "    scp $(id -un)@$(hostname).local:$(basename "$OUT") ."
