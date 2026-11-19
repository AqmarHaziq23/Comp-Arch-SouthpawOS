"""
test_core.py - Southpaw OS Telegram bot

Tests the bot's logic WITHOUT Telegram or a network: messages the bot would send are
collected in a list instead. Run it after changing core.py / storage.py / formatting.py:

    python test_core.py            (prints "ALL TESTS PASSED" if everything works)
"""

import asyncio
import os
import tempfile

from core import SouthpawBot
from formatting import format_round
from storage import Storage

import json

# A round in the hub's format (hub/southpaw/notify.py round_stats)
ROUND = {"round": 1, "rounds": 3, "duration_s": 30, "thrown": 40, "landed": 30}


class Clock:
    """A clock we can move forward by hand (to test the 60 s link timeout instantly)."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def run_tests():
    sent = []                                         # every message the bot "sent": (chat_id, text)

    async def fake_send(chat_id, text):
        sent.append((chat_id, text))

    clock = Clock()
    camera = {"report": None, "photo": None}           # what the fake vision "saved" (None = vision not running)
    bot = SouthpawBot(Storage(os.path.join(tempfile.mkdtemp(), "test.db")), fake_send, 60, clock,
                      read_camera=lambda: (camera["report"], camera["photo"]), wall_clock=lambda: 5000.0)

    # An unknown card, and a round for it: stored, nothing sent
    await bot.card_tapped("04:a1:b2:c3")
    assert sent == []
    assert await bot.round_finished("04A1B2C3", ROUND) is False and sent == []

    # Alice links her card (UID written differently still counts as the same card)
    await bot.start_link(111, "alice", "Alice")
    await bot.card_tapped("04 a1 b2 c3")
    assert sent[-1][0] == 111 and "Card linked" in sent[-1][1]

    # Tapping again just says hello; a finished round goes to Alice with the right accuracy
    await bot.card_tapped("04A1B2C3")
    assert "Punch the bag" in sent[-1][1]
    assert await bot.round_finished("04A1B2C3", ROUND) is True
    assert "Round 1 of 3 complete" in sent[-1][1] and "75%" in sent[-1][1]

    # The NFC reader's own message format ({"uid": ...}, no "kind") counts as a tap too
    await bot.handle_hub_message({"uid": "04A1B2C3"})
    assert "Punch the bag" in sent[-1][1]

    # A /link request expires after 60 s
    await bot.start_link(222, "bob", "Bob")
    clock.t += 61
    await bot.card_tapped("DEADBEEF")
    assert bot.storage.player_for_card("DEADBEEF") is None

    # A second /link takes over the reader, and the first player is told
    await bot.start_link(222, "bob", "Bob")
    await bot.start_link(333, "carol", "Carol")
    assert sent[-1][0] == 222 and "cancelled" in sent[-1][1]

    # Carol taps Alice's card: the card moves to Carol, Alice is told
    await bot.card_tapped("04A1B2C3")
    assert bot.storage.player_for_card("04A1B2C3")["chat_id"] == 333
    assert any(c == 111 and "another Telegram account" in t for c, t in sent[-2:])

    # --- /camera (Carol has Alice's old card now) ---
    def report(**changes):
        """A fresh vision report: everything in view, both hands up. Change any part with keywords."""
        r = {"t": 4999.0, "camera": True, "person": True, "face": True, "hips": True, "fps": 8.9,
             "lead": "L", "image": True, "hands": {"L": {"seen": True, "up": True}, "R": {"seen": True, "up": True}}}
        r.update(changes)
        return r

    # Only chats with a linked card may see camera pictures
    text, photo = bot.camera_reply(999)
    assert photo is None and "link your card first" in text
    # Vision not running: a clear message, no photo; the "card scanned" hello says nothing about the camera
    text, photo = bot.camera_reply(333)
    assert photo is None and "Vision isn't running" in text
    await bot.card_tapped("04A1B2C3")
    assert "Punch the bag" in sent[-1][1] and "Camera" not in sent[-1][1]
    # All in view: the photo comes with the check
    camera["report"], camera["photo"] = report(), b"JPEG"
    text, photo = bot.camera_reply(333)
    assert photo == b"JPEG" and "I can see you" in text and "All set" in text and "8.9 checks/s" in text
    await bot.card_tapped("04A1B2C3")
    assert "Camera: ✅ sees you and both hands" in sent[-1][1]
    # Left (lead) hand out of view, hips too
    camera["report"] = report(hips=False, hands={"L": {"seen": False, "up": None}, "R": {"seen": True, "up": False}})
    text, photo = bot.camera_reply(333)
    assert "Left hand (lead): can't see it" in text and "Right hand (rear): seen (down" in text
    assert "hips" in text and "All set" not in text
    await bot.card_tapped("04A1B2C3")
    assert "can't see your left hand or your hips" in sent[-1][1]
    # Nobody in view
    camera["report"] = report(person=False)
    assert "can't see anyone" in bot.camera_reply(333)[0]
    # An old picture (vision stopped) is never sent; neither is one when the camera is gone
    camera["report"] = report(t=4900.0)
    text, photo = bot.camera_reply(333)
    assert photo is None and "Vision stopped about 100 s ago" in text
    await bot.card_tapped("04A1B2C3")
    assert "Camera" not in sent[-1][1]
    camera["report"] = report(camera=False, image=False)
    text, photo = bot.camera_reply(333)
    assert photo is None and "isn't sending pictures" in text
    # A slow Pi gets a warning; a broken camera reader never crashes the bot
    camera["report"] = report(fps=3.2)
    assert "Only 3.2 checks a second" in bot.camera_reply(333)[0]
    def broken_camera():
        raise OSError("disk on fire")
    bot.read_camera = broken_camera
    assert "Vision isn't running" in bot.camera_reply(333)[0]
    bot.read_camera = lambda: (None, None)

    # Reading vision's real files: missing folder, a broken report, a good one
    import camera_check
    folder = tempfile.mkdtemp()
    assert camera_check.read_snapshot(os.path.join(folder, "nothing")) == (None, None)
    with open(os.path.join(folder, "latest.json"), "w") as f:
        f.write("{not json")
    assert camera_check.read_snapshot(folder) == (None, None)
    with open(os.path.join(folder, "latest.json"), "w") as f:
        json.dump(report(), f)
    with open(os.path.join(folder, "latest.jpg"), "wb") as f:
        f.write(b"JPEG")
    assert camera_check.read_snapshot(folder) == (report(), b"JPEG")

    # /me, /last, /unlink
    assert "…B2C3" in bot.describe_links(333)
    assert "No card is linked" in bot.describe_links(111)
    assert "Round 1 of 3 complete" in bot.last_round(111, "Alice")
    assert "No rounds yet" in bot.last_round(333, "Carol")
    assert "Unlinked 1" in bot.unlink(333)
    assert bot.storage.player_for_card("04A1B2C3") is None

    # Broken hub messages are ignored (never crash the bot)
    for bad in [None, [], {"kind": "card"}, {"kind": "card", "uid": "not hex!"}, {"kind": "round"}, {"kind": "?"}]:
        await bot.handle_hub_message(bad)

    # If Telegram refuses a message (e.g. player blocked the bot), the bot carries on
    async def broken_send(chat_id, text):
        raise RuntimeError("Forbidden: bot was blocked by the user")
    bot.send = broken_send
    await bot.start_link(444, "dan", "Dan")
    await bot.card_tapped("CAFE01")
    assert await bot.round_finished("CAFE01", ROUND) is False

    # Formatting copes with empty data and escapes anything that looks like HTML
    assert "No stats" in format_round({}, None)
    assert "&lt;Bob&gt;" in format_round({"thrown": 5}, "<Bob>")

    # The sample round shows every line; a shaky heart-rate reading is flagged;
    # stats the bot doesn't know yet (e.g. future guard stats) still appear
    with open("sample_round.json") as f:
        full = format_round(json.load(f), "Alice")
    for expected in ("Score: <b>120</b>", "14 landed / 18 thrown (78%)", "Combos: 4/6 correct",
                     "Best streak: 3", "Rest heart rate: 142 bpm", "Round time: 0:30", "Uppercut"):
        assert expected in full, expected
    assert "rough reading" in format_round({"hr_bpm": 130, "hr_good": False})
    assert "Total jabs: 7" in format_round({"total_jabs": 7})

    # Guard stats from vision get their own line; a single drop isn't "1 drops"
    assert "Guard up: 84% (lead 90% · rear 86%) · 2 drops" in full
    assert "Guard up: 100% · 1 drop" in format_round({"guard_pct": 100, "guard_drops": 1})
    assert "Guard" not in format_round({"score": 5})       # no vision: no guard line


if __name__ == "__main__":
    asyncio.run(run_tests())
    print("ALL TESTS PASSED")
