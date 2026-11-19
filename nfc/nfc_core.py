"""
nfc_core.py - Southpaw OS, NFC card reader (Member D)

The parts of the NFC reader that don't need the hardware, so they can be tested
on any computer (test_nfc.py):

  * format_uid()  turns the card's raw ID bytes into text, e.g. b'\\x04\\xa2\\xb9\\x1c' -> "04A2B91C"
  * TapFilter     decides whether a card read is a NEW tap or the same card still
                  lying on the reader (the reader sees a card several times a second)
  * TapSender     sends each tap to the hub AND to the Telegram bot

Where taps go (one JSON object per UDP datagram, protocol/PROTOCOL.md "Pi-local inputs"):

  127.0.0.1:5006  hub           {"uid": "04A2B91C"}   -> this card is the player for the next game
  127.0.0.1:5007  Telegram bot  {"uid": "04A2B91C"}   -> links the card after /link, or says "hi"

Why UDP (same as vision/sender.py): fire-and-forget. The reader never waits for the
hub or the bot, and keeps working if one of them isn't running (every input is optional).
"""

import json
import socket
import time

HUB_PORT = 5006
BOT_PORT = 5007


def format_uid(raw):
    """Card ID bytes -> upper-case hex text with no spaces, e.g. bytearray(b'\\x04\\xa2') -> "04A2".

    This is the same form the bot stores (telegram_bot/storage.py normalise_uid)
    and the example in PROTOCOL.md, so the hub, the bot and the website all agree."""
    if not raw:
        raise ValueError("empty card ID")
    return bytes(raw).hex().upper()


class TapFilter:
    """Turns a stream of card reads into taps.

    The reader is asked "is there a card?" about 5 times a second. While a card is
    held on it, every one of those reads finds the card, but it's ONE tap.

    Rule: a read counts as a new tap unless the SAME card was also seen less than
    `gap_s` seconds ago. Because every read refreshes the "last seen" time, a card
    left lying on the reader never counts again; once it has been away for `gap_s`
    seconds, tapping it again counts. A different card always counts straight away."""

    def __init__(self, gap_s=2.0):
        self.gap_s = gap_s
        self.last_uid = None     # the card seen most recently
        self.last_seen = None    # when it was seen (time.monotonic() seconds)

    def is_new_tap(self, uid, now=None):
        now = time.monotonic() if now is None else now
        repeat = (uid == self.last_uid and self.last_seen is not None
                  and now - self.last_seen < self.gap_s)
        # remember this read either way, so a card left on the reader keeps being "recent"
        self.last_uid, self.last_seen = uid, now
        return not repeat


class TapSender:
    """Sends {"uid": ...} to the hub and the bot. Never raises: a missing listener is fine."""

    def __init__(self, host="127.0.0.1", ports=(HUB_PORT, BOT_PORT)):
        self.addresses = [(host, int(p)) for p in ports]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent = 0
        self.errors = 0

    def send_tap(self, uid):
        data = json.dumps({"uid": uid}).encode("utf-8")
        for address in self.addresses:
            try:
                self.sock.sendto(data, address)
                self.sent += 1
            except OSError:
                # e.g. network down; with UDP to 127.0.0.1 this almost never happens.
                # Nobody listening is NOT an error for UDP: the packet is just dropped.
                self.errors += 1

    def close(self):
        self.sock.close()
