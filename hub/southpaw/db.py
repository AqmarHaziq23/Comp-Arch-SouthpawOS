"""SQLite store for players, sessions, punches and heart rates.

The session logs (logs/*.jsonl) keep every raw message. This database keeps
the summaries the dashboard shows: who played, their scores, which punches
landed and how their heart rate recovered.

Only the hub writes to it. The dashboard should open it read-only:

    sqlite3.connect("file:southpaw.db?mode=ro", uri=True)

WAL mode lets the dashboard read while the hub writes.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .fusion import FusedPunch

GUEST = "guest"  # player uid when nobody tapped an NFC card

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    uid        TEXT PRIMARY KEY,          -- NFC card UID, or 'guest'
    name       TEXT NOT NULL,
    stance     TEXT NOT NULL DEFAULT 'orthodox' CHECK (stance IN ('orthodox', 'southpaw')),
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    player_uid  TEXT NOT NULL REFERENCES players(uid),
    source      TEXT NOT NULL CHECK (source IN ('ble', 'sim', 'replay')),
    started_at  REAL NOT NULL,            -- unix time
    ended_at    REAL,
    total_score INTEGER
);

CREATE TABLE IF NOT EXISTS punches (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES sessions(id),
    round           INTEGER NOT NULL,
    t               REAL NOT NULL,        -- hub clock (seconds), for ordering within a session
    hand            TEXT NOT NULL,
    type            TEXT NOT NULL,        -- J, C, H, U or X
    peak_g          INTEGER NOT NULL,
    landed          INTEGER NOT NULL,     -- 0 or 1
    impact_strength INTEGER,
    vision_type     TEXT
);

CREATE TABLE IF NOT EXISTS heart_rates (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    round      INTEGER NOT NULL,          -- the rest after this round
    bpm        INTEGER NOT NULL,
    good       INTEGER NOT NULL,
    at         REAL NOT NULL              -- unix time
);

CREATE INDEX IF NOT EXISTS punches_by_session ON punches(session_id);
CREATE INDEX IF NOT EXISTS heart_rates_by_session ON heart_rates(session_id);
"""


class Store:
    def __init__(self, path: str | Path = "southpaw.db") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.add_player(GUEST, "Guest")

    def close(self) -> None:
        self.conn.close()

    # -- players

    def add_player(self, uid: str, name: str, stance: str = "orthodox") -> None:
        """Add a player, or update their name and stance if the card is known."""
        with self.conn:
            self.conn.execute(
                """INSERT INTO players (uid, name, stance, created_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(uid) DO UPDATE SET name = excluded.name, stance = excluded.stance""",
                (uid, name, stance, time.time()),
            )

    def player(self, uid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM players WHERE uid = ?", (uid,)).fetchone()

    # -- sessions

    def start_session(self, player_uid: str | None, source: str) -> int:
        uid = player_uid or GUEST
        if self.player(uid) is None:  # unknown card: register it so it can be named later
            self.add_player(uid, f"Player {uid[-4:]}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sessions (player_uid, source, started_at) VALUES (?, ?, ?)",
                (uid, source, time.time()),
            )
        return cur.lastrowid

    def end_session(self, session_id: int, total_score: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended_at = ?, total_score = ? WHERE id = ?",
                (time.time(), total_score, session_id),
            )

    def add_punches(self, session_id: int, round_no: int, punches: list[FusedPunch]) -> None:
        with self.conn:
            self.conn.executemany(
                """INSERT INTO punches
                   (session_id, round, t, hand, type, peak_g, landed, impact_strength, vision_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (session_id, round_no, p.t, p.hand, p.type, p.peak_g, int(p.landed), p.impact_strength, p.vision_type)
                    for p in punches
                ],
            )

    def add_heart_rate(self, session_id: int, round_no: int, bpm: int, good: bool) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO heart_rates (session_id, round, bpm, good, at) VALUES (?, ?, ?, ?, ?)",
                (session_id, round_no, bpm, int(good), time.time()),
            )

    # -- queries for the dashboard

    def leaderboard(self, limit: int = 10, include_sim: bool = False) -> list[sqlite3.Row]:
        """Each player's best finished session, highest first."""
        sources = "('ble', 'sim', 'replay')" if include_sim else "('ble', 'replay')"
        return self.conn.execute(
            f"""SELECT p.name, p.uid, MAX(s.total_score) AS best, COUNT(s.id) AS sessions
                FROM sessions s JOIN players p ON p.uid = s.player_uid
                WHERE s.total_score IS NOT NULL AND s.source IN {sources}
                GROUP BY p.uid ORDER BY best DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    def session_summary(self, session_id: int) -> dict:
        s = self.conn.execute(
            """SELECT s.*, p.name FROM sessions s JOIN players p ON p.uid = s.player_uid
               WHERE s.id = ?""",
            (session_id,),
        ).fetchone()
        if s is None:
            raise KeyError(f"no session {session_id}")
        counts = self.conn.execute(
            """SELECT COUNT(*) AS thrown, COALESCE(SUM(landed), 0) AS landed
               FROM punches WHERE session_id = ?""",
            (session_id,),
        ).fetchone()
        by_type = self.conn.execute(
            """SELECT type, COUNT(*) AS thrown, SUM(landed) AS landed
               FROM punches WHERE session_id = ? GROUP BY type ORDER BY type""",
            (session_id,),
        ).fetchall()
        hr = self.conn.execute(
            "SELECT round, bpm, good FROM heart_rates WHERE session_id = ? ORDER BY round, at",
            (session_id,),
        ).fetchall()
        return {
            "id": s["id"],
            "player": s["name"],
            "source": s["source"],
            "total_score": s["total_score"],
            "thrown": counts["thrown"],
            "landed": counts["landed"],
            "by_type": [dict(r) for r in by_type],
            "heart_rates": [dict(r) for r in hr],
        }
