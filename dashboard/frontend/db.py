"""The dashboard's database: tables and a few query helpers.

Under Docker Compose this is the Postgres container (DATABASE_URL is set in
docker-compose.yml). Without DATABASE_URL it is a local SQLite file, which is
what `python app.py --sim` and the tests use. The SQL in accounts.py and
store.py is written to run on both.
"""
import os
import time
from pathlib import Path

from sqlalchemy import (Column, Float, ForeignKey, Integer, MetaData, String, Table,
                        UniqueConstraint, create_engine, text)
from sqlalchemy.exc import OperationalError

HERE = Path(__file__).resolve().parent
DEFAULT_URL = "sqlite:///" + (HERE / "dashboard.db").as_posix()

meta = MetaData()
Table("gyms", meta,
      Column("id", Integer, primary_key=True),
      Column("name", String(64), nullable=False, unique=True),
      Column("code", String(16), nullable=False, unique=True))     # athletes type this to join
Table("users", meta,
      Column("id", Integer, primary_key=True),
      Column("username", String(32), nullable=False, unique=True),  # always lower case
      Column("password", String(255), nullable=False),              # salted hash
      Column("role", String(8), nullable=False),                    # coach | athlete
      Column("name", String(32), nullable=False),                   # shown on the dashboard
      Column("gym_id", Integer, ForeignKey("gyms.id"), nullable=False),
      Column("card_uid", String(32), unique=True),                  # athletes: NFC card ID, upper case
      Column("created_at", Float(53), nullable=False))
Table("sessions", meta,
      Column("id", Integer, primary_key=True),
      Column("session_key", String(64), nullable=False, unique=True),  # the Pi's own name for the session
      Column("card_uid", String(32), index=True),                   # NFC card tapped at the Pi; NULL = guest
      Column("started_at", Float(53), nullable=False),              # unix seconds
      Column("updated_at", Float(53), nullable=False))
Table("rounds", meta,
      Column("id", Integer, primary_key=True),
      Column("session_id", Integer, ForeignKey("sessions.id"), nullable=False),
      Column("round_no", Integer, nullable=False),
      Column("score", Integer, nullable=False),
      Column("thrown", Integer, nullable=False),
      Column("landed", Integer, nullable=False),
      Column("combos_called", Integer),
      Column("combos_hit", Integer),
      Column("best_streak", Integer),
      Column("hr_bpm", Integer),                                    # reading taken in the rest after this round
      Column("hr_good", Integer),                                   # 1 good, 0 unreliable
      Column("received_at", Float(53), nullable=False),
      UniqueConstraint("session_id", "round_no"))
Table("meta", meta,
      Column("key", String(32), primary_key=True),
      Column("value", String(255), nullable=False))

engine = None


def init(url=None, wait=30):
    """Connect and create any missing tables. Waits for the database container to come up."""
    global engine
    engine = create_engine(url or os.environ.get("DATABASE_URL") or DEFAULT_URL, pool_pre_ping=True)
    deadline = time.time() + wait
    while True:
        try:
            meta.create_all(engine)
            return
        except OperationalError:
            if time.time() > deadline:
                raise
            time.sleep(1)


def q(con, sql, **params):
    """Run one statement on an open connection. Returns its rows as dicts ([] if it has none)."""
    res = con.execute(text(sql), params)
    return [dict(r) for r in res.mappings()] if res.returns_rows else []


def rows(sql, **params):
    """Run one read query."""
    with engine.connect() as con:
        return q(con, sql, **params)


def begin():
    """`with db.begin() as con:` = one transaction, committed at the end."""
    return engine.begin()
