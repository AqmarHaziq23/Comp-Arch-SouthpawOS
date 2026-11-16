"""
test_nfc.py - Southpaw OS, NFC card reader (Member D)

Tests the parts that don't need the PN532. Run in the nfc folder:
  python test_nfc.py
(no extra packages needed; works on Windows and the Pi)
"""

import json
import socket

from nfc_core import TapFilter, TapSender, format_uid


def test_format_uid():
    assert format_uid(bytearray(b"\x04\xa2\xb9\x1c")) == "04A2B91C"           # 4-byte card
    assert format_uid(b"\x04\x11\x22\x33\x44\x55\x66") == "04112233445566"     # 7-byte card
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


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"all {len(tests)} tests passed")
