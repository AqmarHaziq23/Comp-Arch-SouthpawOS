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

ROUND = {"duration_s": 125, "punches_thrown": 40, "punches_landed": 30}


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
    bot = SouthpawBot(Storage(os.path.join(tempfile.mkdtemp(), "test.db")), fake_send, 60, clock)

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
    assert "Round complete" in sent[-1][1] and "75%" in sent[-1][1]

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

    # /me, /last, /unlink
    assert "…B2C3" in bot.describe_links(333)
    assert "No card is linked" in bot.describe_links(111)
    assert "Round complete" in bot.last_round(111, "Alice")
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
    assert "&lt;Bob&gt;" in format_round({"punches_thrown": 5}, "<Bob>")


if __name__ == "__main__":
    asyncio.run(run_tests())
    print("ALL TESTS PASSED")
