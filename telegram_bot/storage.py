"""
storage.py - Southpaw OS Telegram bot

Keeps the bot's data in a small local SQLite database file (players.db by default).
SQLite is built into Python, so there is nothing extra to install.

Two tables:
  cards   - which NFC card belongs to which Telegram chat (the "link")
  rounds  - every round summary the bot has received (so players can ask for /last)

Card UIDs are stored in one standard form (upper-case hex, no spaces or colons),
so "04:a1:b2:c3" and "04A1B2C3" count as the same card.
"""

import json
import sqlite3
import time


def normalise_uid(uid):
    """Turn any way of writing a card UID into one standard form, e.g. '04:a1 b2' -> '04A1B2'."""
    cleaned = "".join(ch for ch in str(uid) if ch.isalnum()).upper()
    if not cleaned or any(ch not in "0123456789ABCDEF" for ch in cleaned):
        raise ValueError(f"not a valid card UID: {uid!r}")
    return cleaned


class Storage:
    def __init__(self, path="players.db"):
        # check_same_thread=False: the bot only uses this from one thread at a time, so it's safe
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row          # rows behave like dicts: row["chat_id"]
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cards (
                uid        TEXT PRIMARY KEY,   -- NFC card UID (one card = one player)
                chat_id    INTEGER NOT NULL,   -- Telegram chat to send this player's results to
                username   TEXT,               -- Telegram @username (may be empty; can change)
                first_name TEXT,               -- for friendly messages
                linked_at  REAL NOT NULL       -- when the card was linked (Unix time)
            );
            CREATE TABLE IF NOT EXISTS rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                uid         TEXT NOT NULL,     -- card that played the round
                chat_id     INTEGER,           -- who it was sent to (empty if the card wasn't linked)
                received_at REAL NOT NULL,     -- when the hub sent it (Unix time)
                data        TEXT NOT NULL      -- the round's stats, as JSON text
            );
        """)
        self.db.commit()

    # --- Cards ------------------------------------------------------------------------------

    def link_card(self, uid, chat_id, username=None, first_name=None):
        """
        Link a card to a Telegram chat. If the card was linked to someone else before,
        it moves to the new chat. Returns the previous chat_id (or None).
        """
        uid = normalise_uid(uid)
        old = self.db.execute("SELECT chat_id FROM cards WHERE uid = ?", (uid,)).fetchone()
        self.db.execute(
            "INSERT OR REPLACE INTO cards (uid, chat_id, username, first_name, linked_at) VALUES (?, ?, ?, ?, ?)",
            (uid, chat_id, username, first_name, time.time()))
        self.db.commit()
        return old["chat_id"] if old and old["chat_id"] != chat_id else None

    def unlink_chat(self, chat_id):
        """Remove every card linked to this chat. Returns the list of UIDs removed."""
        uids = [r["uid"] for r in self.db.execute("SELECT uid FROM cards WHERE chat_id = ?", (chat_id,))]
        self.db.execute("DELETE FROM cards WHERE chat_id = ?", (chat_id,))
        self.db.commit()
        return uids

    def player_for_card(self, uid):
        """Who owns this card? Returns a dict (chat_id, username, first_name, ...) or None."""
        row = self.db.execute("SELECT * FROM cards WHERE uid = ?", (normalise_uid(uid),)).fetchone()
        return dict(row) if row else None

    def cards_for_chat(self, chat_id):
        """All cards linked to this chat (a player could have more than one)."""
        return [dict(r) for r in self.db.execute("SELECT * FROM cards WHERE chat_id = ? ORDER BY linked_at", (chat_id,))]

    # --- Rounds -----------------------------------------------------------------------------

    def save_round(self, uid, chat_id, data):
        """Store a round summary. Returns its id."""
        cur = self.db.execute("INSERT INTO rounds (uid, chat_id, received_at, data) VALUES (?, ?, ?, ?)",
                              (normalise_uid(uid), chat_id, time.time(), json.dumps(data)))
        self.db.commit()
        return cur.lastrowid

    def last_round_for_chat(self, chat_id):
        """The most recent round sent to this chat, as (received_at, data) - or None."""
        row = self.db.execute("SELECT received_at, data FROM rounds WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
                              (chat_id,)).fetchone()
        return (row["received_at"], json.loads(row["data"])) if row else None

    def close(self):
        self.db.close()
