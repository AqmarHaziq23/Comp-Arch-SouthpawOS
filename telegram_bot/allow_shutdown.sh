#!/usr/bin/env bash
# allow_shutdown.sh - let the Telegram bot's /shutdown switch the Pi off.  Run ONCE on the Pi:
#
#   bash telegram_bot/allow_shutdown.sh            add the permission
#   bash telegram_bot/allow_shutdown.sh --remove   take it away again
#
# Switching the Pi off needs root, and the bot isn't root. This adds a sudo rule that lets THIS
# account (the one the bot runs as) run exactly ONE command without a password:
#     /usr/bin/systemctl poweroff
# Nothing else: every other sudo command still asks for the password.
# Run it as your normal account (not with sudo in front); it asks for your password once.

set -u
ME="$(id -un)"
RULE_FILE="/etc/sudoers.d/southpaw-poweroff"   # no "." in the name: sudo ignores files with one

if [ "$ME" = "root" ]; then
    echo "Run this as the account the bot runs as, without sudo in front."
    exit 1
fi

if [ "${1:-}" = "--remove" ]; then
    sudo rm -f "$RULE_FILE" && echo "Removed: /shutdown in Telegram can't switch the Pi off any more."
    exit 0
fi

# Write the rule to a temporary file and let visudo check it first:
# a broken sudo rule could lock everyone out of sudo, so it's only installed if it's valid.
TMP="$(mktemp)"
echo "$ME ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" > "$TMP"
if sudo visudo -cf "$TMP" > /dev/null; then
    sudo install -m 0440 -o root -g root "$TMP" "$RULE_FILE"
    echo "Done: the bot (account $ME) may now switch the Pi off with /shutdown."
    echo "Check (prints the rule, changes nothing):  sudo -l -U $ME | grep poweroff"
else
    echo "The rule didn't pass visudo's check, so nothing was changed."
fi
rm -f "$TMP"
