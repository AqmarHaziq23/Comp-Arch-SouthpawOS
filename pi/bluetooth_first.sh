#!/usr/bin/env bash
# bluetooth_first.sh - SouthpawOS: let the boards connect while the Pi's Wi-Fi is off. (Member D)
#
# Why: on a Pi 4, Wi-Fi and Bluetooth share ONE chip and ONE antenna. When the Pi's Wi-Fi is on
# 2.4 GHz (e.g. a phone hotspot with "Maximize Compatibility" on) in a room full of Bluetooth
# devices, the boards can't finish connecting: every try ends with "Connection Failed to be
# Established (0x3e)" (seen with btmon). Once a board IS connected it usually stays connected.
# So: Wi-Fi off -> the boards connect -> Wi-Fi back on.
#
# What it does:
#   1. switches the Pi's Wi-Fi off
#   2. waits until the hub prints "all boards connected", or BT_FIRST_SECS seconds at most
#   3. switches Wi-Fi back on - ALWAYS, also if this script is stopped or something fails,
#      so SSH and the bot never stay offline
# While Wi-Fi is off: no SSH over Wi-Fi, the bot can't reach Telegram and nothing is uploaded.
#
#   At boot:  the southpaw-btfirst service runs it once (pi/install.sh sets that up).
#             Switch the 3 boards on within the first ~3 minutes after switching the Pi on.
#   By hand:  a board dropped and won't come back? Switch the boards off, run
#                 sudo bash ~/southpaw/pi/bluetooth_first.sh --restart-hub
#             and switch the boards on straight away. (SSH freezes until the boards are connected.)
#
# Settings (pi/southpaw.env): BT_FIRST_SECS=180 (how long to wait at most), BT_FIRST_SECS=0 (off).

set -u

MAX_SECS="${BT_FIRST_SECS:-180}"

if [ "$(id -u)" != "0" ]; then
    echo "Needs root (to switch Wi-Fi off and on):  sudo bash $0 $*"
    exit 1
fi
if [ "$MAX_SECS" = "0" ]; then
    echo "BT_FIRST_SECS=0: switched off, Wi-Fi stays on"
    exit 0
fi
if ! systemctl is-enabled --quiet southpaw-hub 2>/dev/null; then
    echo "The hub service isn't installed (pi/install.sh): nothing to wait for, Wi-Fi stays on"
    exit 0
fi

# Step 3 first: whatever happens from here on (normal end, error, Ctrl+C, systemctl stop),
# Wi-Fi is switched back on when this script ends.
wifi_on() {
    rfkill unblock wifi
    echo "Wi-Fi back on"
}
trap wifi_on EXIT
trap 'exit 1' INT TERM HUP     # without this, bash skips the EXIT trap when it's stopped by a signal

START="$(date +%s)"            # only look at hub messages from now on

# Right after boot the Wi-Fi chip may not exist yet, and "rfkill block" would switch off nothing.
# So first wait (up to 30 s) until rfkill can see a Wi-Fi device.
for _ in $(seq 30); do
    [ -n "$(rfkill list wifi 2>/dev/null)" ] && break
    sleep 1
done

echo "Wi-Fi off: waiting up to ${MAX_SECS} s for the boards to connect"
rfkill block wifi

# By hand: start the hub again, so it begins with a fresh Bluetooth and no half-connected boards
if [ "${1:-}" = "--restart-hub" ]; then
    systemctl restart southpaw-hub
fi

# Step 2: has the hub said "all boards connected" since we started? Check every 2 s.
while [ $(( $(date +%s) - START )) -lt "$MAX_SECS" ]; do
    if journalctl -u southpaw-hub --since "@$START" --no-pager -q 2>/dev/null | grep -q "all boards connected"; then
        echo "All boards connected after $(( $(date +%s) - START )) s"
        exit 0                 # the trap switches Wi-Fi back on
    fi
    # Did something else switch Wi-Fi back on (e.g. NetworkManager starting up after us)? Off again.
    if ! rfkill list wifi | grep -q "Soft blocked: yes"; then
        echo "Wi-Fi was switched on by something else; switching it off again"
        rfkill block wifi
    fi
    sleep 2
done

echo "Not all boards connected within ${MAX_SECS} s (switched on? charged?)."
echo "Try again by hand:  sudo bash $0 --restart-hub"
exit 0
