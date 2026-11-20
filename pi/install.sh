#!/usr/bin/env bash
# install.sh - SouthpawOS: make the Pi start everything by itself when it's switched on.
#
# Installs 4 systemd services, all running as the account that runs this script:
#   southpaw-hub     the game (boards, NFC taps, vision, Telegram)   hub/
#   southpaw-bot     the Telegram bot                                telegram_bot/
#   southpaw-nfc     the NFC card reader                             nfc/
#   southpaw-vision  the webcam guard tracking                       vision/
# plus southpaw-btfirst: at boot, Wi-Fi stays off until the boards have connected (pi/bluetooth_first.sh).
# A part that isn't set up yet (no .venv, no boards.toml, no bot token) is skipped with a message,
# so you can run this again later once it is.
#
#   bash pi/install.sh            install (or update) and start them all
#   bash pi/install.sh --remove   stop them and remove them again (back to starting things by hand)
#
# Run it as your normal user (not with sudo in front): it asks for sudo itself where needed.

set -u

# --- where are we? -------------------------------------------------------------------------
PI_DIR="$(cd "$(dirname "$0")" && pwd)"     # .../pi
DIR="$(dirname "$PI_DIR")"                  # the repo folder (hub/, nfc/, vision/, ... are in here)
ME="$(id -un)"                              # the account the services will run as
SERVICES="southpaw-hub southpaw-bot southpaw-nfc southpaw-vision southpaw-btfirst"

if [ "$ME" = "root" ]; then
    echo "Run this as your normal account, without sudo in front:  bash pi/install.sh"
    exit 1
fi

# --- remove ----------------------------------------------------------------------------------
if [ "${1:-}" = "--remove" ]; then
    for s in $SERVICES; do
        sudo systemctl disable --now "$s" 2>/dev/null
        sudo rm -f "/etc/systemd/system/$s.service"
        echo "removed $s"
    done
    sudo systemctl daemon-reload
    echo "Done. Nothing starts by itself any more; start the programs by hand again."
    exit 0
fi

# --- settings file ---------------------------------------------------------------------------
# First time only: make pi/southpaw.env from the example (game length, website token, ...)
if [ ! -f "$PI_DIR/southpaw.env" ]; then
    cp "$PI_DIR/southpaw.env.example" "$PI_DIR/southpaw.env"
    echo "made pi/southpaw.env (settings; change it any time, then restart the service)"
fi

# --- is each part ready to run? ----------------------------------------------------------------
# Prints why not, and returns "not ready" so that service is skipped.
ready() {
    case "$1" in
        southpaw-hub)
            [ -x "$DIR/hub/.venv/bin/python" ] || { echo "  no hub/.venv (see pi/README.md step 2)"; return 1; }
            [ -f "$DIR/hub/boards.toml" ]      || { echo "  no hub/boards.toml (cp hub/boards.example.toml hub/boards.toml)"; return 1; } ;;
        southpaw-bot)
            [ -x "$DIR/telegram_bot/.venv/bin/python" ] || { echo "  no telegram_bot/.venv"; return 1; }
            [ -f "$DIR/telegram_bot/.env" ]             || { echo "  no telegram_bot/.env (the bot token)"; return 1; } ;;
        southpaw-nfc)
            [ -x "$DIR/nfc/.venv/bin/python" ] || { echo "  no nfc/.venv"; return 1; } ;;
        southpaw-vision)
            [ -x "$DIR/vision/.venv/bin/python" ] || { echo "  no vision/.venv (uv venv --python 3.12, see vision/README.md)"; return 1; } ;;
        southpaw-btfirst)
            ready southpaw-hub > /dev/null || { echo "  needs the hub set up first"; return 1; } ;;
    esac
    return 0
}

# --- install each service --------------------------------------------------------------------
installed=""
for s in $SERVICES; do
    if ! ready "$s"; then
        echo "SKIPPED $s (fix the line above, then run this script again)"
        continue
    fi
    # Fill in the account and the folder, and put the finished file where systemd looks for it
    sed -e "s|@USER@|$ME|g" -e "s|@DIR@|$DIR|g" "$PI_DIR/services/$s.service" \
        | sudo tee "/etc/systemd/system/$s.service" > /dev/null
    installed="$installed $s"
done

if [ -z "$installed" ]; then
    echo "Nothing installed."
    exit 1
fi

# Tell systemd about the new files, switch them on for every boot, and (re)start them now
sudo systemctl daemon-reload
for s in $installed; do
    sudo systemctl enable "$s" > /dev/null 2>&1
    # btfirst switches Wi-Fi off, which would cut this SSH session: it only runs at the next boot
    [ "$s" = "southpaw-btfirst" ] || sudo systemctl restart "$s"
done

sleep 3
echo
echo "Running now (and from now on at every boot):"
for s in $installed; do
    if [ "$s" = "southpaw-btfirst" ]; then
        printf "  %-17s %s\n" "$s" "enabled: runs at the next boot (Wi-Fi off until the boards connect)"
    else
        printf "  %-17s %s\n" "$s" "$(systemctl is-active "$s")"
    fi
done
echo
echo "Watch one live:   journalctl -u southpaw-hub -f      (Ctrl+C stops watching, not the program)"
echo "Everything:       journalctl -u 'southpaw-*' -f"
