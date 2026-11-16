"""Tell Johanan's Telegram bot (telegram_bot/bot.py) about every finished round,
so the player gets their results on their phone.

The bot is a separate program on the same Pi. The hub sends it one JSON object
per UDP datagram to 127.0.0.1:5007 (see protocol/PROTOCOL.md):

    {"v": 1, "src": "hub", "kind": "round", "uid": "04A2B91C",
     "round": {"round": 1, "rounds": 3, "score": 120, "thrown": 18, "landed": 14, ...}}

UDP means "send and forget": if the bot isn't running, the message is simply
lost and the game carries on (every input/output is optional). Guest rounds
(no card tapped) are not sent, because the bot can't know whose phone to use.
"""

from __future__ import annotations

import json
import logging
import socket

from . import protocol as proto

log = logging.getLogger(__name__)

BOT_PORT = 5007

# Punch letters from the protocol -> the words the bot shows ("J" -> "jab")
_PUNCH_WORDS = {code: name.lower() for code, name in proto.PUNCH_CODES.items()}


def round_stats(r, rounds_total: int, round_secs: float) -> dict:
    """Everything the bot shows about one round, as plain JSON-friendly values.

    `r` is a game.RoundResult. The names match what the website gets from
    pi_uploader (score, thrown, landed, combos_called, ...) so both stay in step."""
    # Count thrown and landed per punch type, e.g. {"jab": {"thrown": 5, "landed": 4}, ...}
    by_type: dict[str, dict[str, int]] = {}
    for combo in r.combos:
        for p in combo.punches:
            stats = by_type.setdefault(_PUNCH_WORDS.get(p.type, "unknown"), {"thrown": 0, "landed": 0})
            stats["thrown"] += 1
            stats["landed"] += int(p.landed)

    stats = {
        "round": r.number,
        "rounds": rounds_total,
        "duration_s": round_secs,
        "score": r.score,
        "thrown": r.thrown,
        "landed": r.landed,
        "combos_called": len(r.combos),
        "combos_hit": r.combos_hit,
        "best_streak": r.best_streak,
        "by_type": by_type,
    }
    if r.hr_bpm is not None:  # rest heart rate after this round, only if the sensor got one
        stats["hr_bpm"] = r.hr_bpm
        stats["hr_good"] = r.hr_good
    if r.guard:  # guard_pct, guard_pct_lead, guard_pct_rear, guard_drops (only if vision was running)
        stats.update({k: v for k, v in r.guard.items() if v is not None})
    return stats


class BotNotifier:
    """Sends round results to the Telegram bot over UDP. Never raises."""

    def __init__(self, host: str = "127.0.0.1", port: int = BOT_PORT) -> None:
        self.addr = (host, port)
        # One socket for the whole game; UDP needs no connection, so this can't block
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_round(self, uid: str | None, stats: dict) -> bool:
        """Send one finished round. Returns False for guests or if sending failed."""
        if not uid:
            return False  # guest: nobody to send it to
        message = {"v": 1, "src": "hub", "kind": "round", "uid": uid, "round": stats}
        try:
            self._sock.sendto(json.dumps(message).encode("utf-8"), self.addr)
            return True
        except OSError as e:  # e.g. network stack hiccup: log it, the game must keep going
            log.warning("could not send round to the Telegram bot: %s", e)
            return False

    def close(self) -> None:
        self._sock.close()
