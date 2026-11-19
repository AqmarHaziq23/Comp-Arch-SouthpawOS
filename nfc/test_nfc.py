"""
test_nfc.py - Southpaw OS, NFC card reader (Member D)

Tests the parts that don't need the PN532. Run in the nfc folder:
  python test_nfc.py
(no extra packages needed; works on Windows and the Pi)
"""

import itertools
import json
import logging
import socket

from nfc_core import TapFilter, TapSender, format_uid
from nfc_reader import read_cards, run

logging.disable(logging.WARNING)   # the reconnect tests cause warnings on purpose; keep the output clean

FOB = b"\xf6\xd9\x4a\x06"                       # the blue test key fob (4-byte ID)
SEVEN = b"\x04\x11\x22\x33\x44\x55\x66"         # a made-up card with a 7-byte ID


def test_format_uid():
    assert format_uid(bytearray(b"\x04\xa2\xb9\x1c")) == "04A2B91C"           # 4-byte card
    assert format_uid(bytearray(FOB)) == "F6D94A06"                            # the blue fob, as the Pi printed it
    assert format_uid(SEVEN) == "04112233445566"                               # 7-byte card (14 characters)
    for empty in (None, b"", bytearray()):
        try:
            format_uid(empty)
            raise AssertionError("an empty ID should be refused")
        except ValueError:
            pass


def test_card_held_on_reader_is_one_tap():
    f = TapFilter(gap_s=2.0)
    # the reader sees the same card 5 times a second for 3 seconds
    reads = [f.is_new_tap("AA", now=10 + i * 0.2) for i in range(15)]
    assert reads.count(True) == 1 and reads[0] is True


def test_same_card_again_after_the_gap():
    f = TapFilter(gap_s=2.0)
    assert f.is_new_tap("AA", now=10.0)
    assert not f.is_new_tap("AA", now=11.5)   # 1.5 s later: still the same tap
    assert f.is_new_tap("AA", now=14.0)       # away 2.5 s, tapped again: counts


def test_different_card_counts_straight_away():
    f = TapFilter(gap_s=2.0)
    assert f.is_new_tap("AA", now=10.0)
    assert f.is_new_tap("BB", now=10.2)
    assert f.is_new_tap("AA", now=10.4)       # back to the first card: a new tap too


def test_gap_zero_counts_every_read():
    f = TapFilter(gap_s=0)                    # what --fake uses: one Enter = one tap
    assert all(f.is_new_tap("AA", now=10.0 + i * 0.01) for i in range(3))


def test_sender_reaches_hub_and_bot():
    # two listeners on free ports, standing in for the hub and the bot
    listeners = []
    for _ in range(2):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))              # port 0 = "any free port"
        s.settimeout(2)
        listeners.append(s)
    ports = [s.getsockname()[1] for s in listeners]

    sender = TapSender("127.0.0.1", ports)
    sender.send_tap("04A2B91C")
    for s in listeners:
        data, _ = s.recvfrom(1024)
        assert json.loads(data) == {"uid": "04A2B91C"}   # exactly the PROTOCOL.md format
        s.close()
    assert sender.sent == 2 and sender.errors == 0
    sender.close()


def test_sender_without_listeners_does_not_crash():
    sender = TapSender("127.0.0.1", (1, 2))   # nothing listens there
    sender.send_tap("04A2B91C")               # must just carry on
    sender.close()


# ------------------------------------------------------------ the reader loop, with a fake PN532

class FakePN532:
    """Stands in for the real reader: each read_passive_target() gives the next item of
    `script`: card bytes, None (no card), or an exception to raise (like an I2C error)."""

    def __init__(self, script):
        self.script = list(script)

    def read_passive_target(self, timeout):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fake_connect(*attempts):
    """A connect() for read_cards: each call gives the next attempt, a FakePN532 or an exception."""
    attempts = list(attempts)

    def connect():
        a = attempts.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    return connect


def test_reader_recovers_from_i2c_errors():
    """Not plugged in at first, then cards, then a loose wire mid-read, then working again."""
    waits = []
    connect = fake_connect(
        OSError("no PN532 at 0x24"),                          # 1st try: reader not there
        FakePN532([None, FOB, None, OSError("I2C error")]),   # works, then fails mid-read
        FakePN532([SEVEN]),                                   # reconnected
    )
    cards = read_cards(connect, sleep=waits.append)           # sleep is faked: the test doesn't wait
    seen = list(itertools.islice(cards, 4))                   # the first 4 reads that come out
    assert seen == [None, "F6D94A06", None, "04112233445566"]
    assert waits == [3.0, 3.0]                                # it waited before each retry


def test_run_sends_each_new_tap_to_hub_and_bot():
    # two listeners on free ports, standing in for the hub (5006) and the bot (5007)
    listeners = []
    for _ in range(2):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        s.settimeout(2)
        listeners.append(s)
    sender = TapSender("127.0.0.1", [s.getsockname()[1] for s in listeners])

    # the fob held on the reader for 3 reads (= one tap), nothing, then the 7-byte card
    connect = fake_connect(FakePN532([FOB, FOB, FOB, None, SEVEN]))
    cards = itertools.islice(read_cards(connect), 5)
    assert run(cards, TapFilter(gap_s=2.0), sender) == 2

    for s in listeners:   # each listener got exactly these two messages, in order
        got = [json.loads(s.recvfrom(1024)[0]) for _ in range(2)]
        assert got == [{"uid": "F6D94A06"}, {"uid": "04112233445566"}]
        s.close()
    sender.close()


def test_dry_run_sends_nothing():
    assert run(iter(["F6D94A06", None, "62685906"]), TapFilter(gap_s=2.0), sender=None) == 2


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"all {len(tests)} tests passed")
