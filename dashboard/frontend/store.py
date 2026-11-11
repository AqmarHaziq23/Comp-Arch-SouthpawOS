"""Sessions and rounds: what the Pi uploads, and what the pages read.

The Pi posts one JSON object after every round (ingest_round). A round carries
the NFC card ID that was tapped; a session belongs to whichever athlete account
has that card. Every read below is limited to one gym in the SQL itself.
"""
import random
import time

import db


def _acc(landed, thrown):
    return round(100.0 * landed / thrown) if thrown else 0


# ---------- upload from the Pi ----------
def _int(p, key, default=None, low=0, high=1_000_000):
    v = p.get(key)
    if v is None:
        if default is None:
            raise ValueError(f'"{key}" is required.')
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
        raise ValueError(f'"{key}" must be a number from {low} to {high}.')
    return int(v)


def ingest_round(p):
    """Save one finished round. Returns the session id. Raises ValueError for bad data.

    {"session": "pi-20261111-153000",   required: the Pi's name for this session, same for all its rounds
     "round": 1,                        required: 1, 2, 3...
     "card": "04A2B91C",                NFC card ID; leave out for a guest
     "score": 120, "thrown": 18, "landed": 14,
     "combos_called": 6, "combos_hit": 2, "best_streak": 3,      optional
     "hr": {"bpm": 132, "good": true},                           optional: rest reading after the round
     "started_at": 1794379269.0}                                 optional: session start, unix seconds

    Sending the same session + round again replaces it, so the Pi can retry safely."""
    if not isinstance(p, dict):
        raise ValueError("Send one JSON object per round.")
    key = " ".join(str(p.get("session") or "").split())[:64]
    if not key:
        raise ValueError('"session" is required.')
    card = "".join(str(p.get("card") or "").split()).upper()[:32] or None
    round_no = _int(p, "round", low=1, high=99)
    score, thrown, landed = _int(p, "score", 0), _int(p, "thrown", 0), _int(p, "landed", 0)
    if landed > thrown:
        raise ValueError('"landed" cannot be more than "thrown".')
    extras = [None if p.get(k) is None else _int(p, k) for k in ("combos_called", "combos_hit", "best_streak")]
    hr = p.get("hr") if isinstance(p.get("hr"), dict) else {}
    bpm = _int(hr, "bpm", low=20, high=250) if hr.get("bpm") is not None else None
    now = time.time()
    started = p.get("started_at")
    started = float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else now
    with db.begin() as con:
        found = db.q(con, "SELECT id, card_uid FROM sessions WHERE session_key = :k", k=key)
        if found:
            sid = found[0]["id"]
            db.q(con, "UPDATE sessions SET updated_at = :t, card_uid = :c WHERE id = :i",
                 t=now, c=found[0]["card_uid"] or card, i=sid)
        else:
            sid = db.q(con, """INSERT INTO sessions (session_key, card_uid, started_at, updated_at)
                               VALUES (:k, :c, :s, :t) RETURNING id""", k=key, c=card, s=started, t=now)[0]["id"]
        db.q(con, "DELETE FROM rounds WHERE session_id = :s AND round_no = :n", s=sid, n=round_no)
        db.q(con,
             """INSERT INTO rounds (session_id, round_no, score, thrown, landed, combos_called, combos_hit,
                                    best_streak, hr_bpm, hr_good, received_at)
                VALUES (:s, :n, :score, :thrown, :landed, :cc, :ch, :streak, :bpm, :good, :t)""",
             s=sid, n=round_no, score=score, thrown=thrown, landed=landed,
             cc=extras[0], ch=extras[1], streak=extras[2],
             bpm=bpm, good=None if bpm is None else int(bool(hr.get("good", True))), t=now)
    return sid


# ---------- reads (always one gym) ----------
_SESSION_SQL = """
SELECT s.id, s.started_at, u.username, u.name AS nickname,
       COUNT(r.id) AS rounds, COALESCE(SUM(r.score), 0) AS score,
       COALESCE(SUM(r.thrown), 0) AS thrown, COALESCE(SUM(r.landed), 0) AS landed,
       COALESCE(MAX(r.best_streak), 0) AS best_streak
FROM sessions s
JOIN users u ON u.card_uid = s.card_uid
LEFT JOIN rounds r ON r.session_id = s.id
WHERE u.gym_id = :gym {extra}
GROUP BY s.id, s.started_at, u.username, u.name
ORDER BY s.started_at DESC, s.id DESC
LIMIT :limit
"""


def _sessions(gym_id, extra="", limit=100000, **params):
    out = db.rows(_SESSION_SQL.format(extra=extra), gym=gym_id, limit=limit, **params)
    for s in out:
        s["player_id"] = s.pop("username").upper()
        s["accuracy"] = _acc(s["landed"], s["thrown"])
    return out


def recent_sessions(gym_id, limit=50):
    """Newest sessions of the gym's athletes."""
    return _sessions(gym_id, limit=limit)


def leaderboard(athletes, gym_id):
    """One row per athlete of the gym (accounts.athletes), best score first."""
    board = {a["player_id"]: {"id": a["player_id"], "nickname": a["name"], "sessions": 0,
                              "best_score": 0, "landed": 0, "thrown": 0} for a in athletes}
    for s in _sessions(gym_id):
        d = board.get(s["player_id"])
        if d:
            d["sessions"] += 1
            d["best_score"] = max(d["best_score"], s["score"])
            d["landed"] += s["landed"]
            d["thrown"] += s["thrown"]
    for d in board.values():
        d["accuracy"] = _acc(d["landed"], d["thrown"])
    return sorted(board.values(), key=lambda d: (-d["best_score"], d["nickname"].lower()))


def session_detail(sid, gym_id):
    """One session with its rounds, or None if it is not one of this gym's."""
    found = _sessions(gym_id, "AND s.id = :sid", sid=sid)
    if not found:
        return None
    rounds = db.rows(
        """SELECT round_no, score, thrown, landed, combos_called, combos_hit, hr_bpm, hr_good
           FROM rounds WHERE session_id = :s ORDER BY round_no""", s=sid)
    for r in rounds:
        r["accuracy"] = _acc(r["landed"], r["thrown"])
    hrs = [{"round_no": r["round_no"], "bpm": r["hr_bpm"], "good": r["hr_good"]}
           for r in rounds if r["hr_bpm"] is not None]
    return {"session": found[0], "rounds": rounds, "hr": hrs}


def latest_round(gym_id):
    """The round uploaded most recently for one of this gym's athletes, with its
    session's running total, or None. This is what the /live page shows."""
    found = db.rows(
        """SELECT r.id, r.session_id, r.round_no, r.score, r.thrown, r.landed, r.combos_called, r.combos_hit,
                  r.best_streak, r.hr_bpm, r.hr_good, r.received_at, u.username, u.name AS nickname
           FROM rounds r JOIN sessions s ON s.id = r.session_id JOIN users u ON u.card_uid = s.card_uid
           WHERE u.gym_id = :gym
           ORDER BY r.received_at DESC, r.id DESC LIMIT 1""", gym=gym_id)
    if not found:
        return None
    r = found[0]
    r["player_id"] = r.pop("username").upper()
    r["accuracy"] = _acc(r["landed"], r["thrown"])
    r["session"] = _sessions(gym_id, "AND s.id = :sid", sid=r["session_id"])[0]
    return r


def hr_recovery(hrs):
    """Average of the good rest readings (one per rest period), or None."""
    good = [h["bpm"] for h in hrs if h["good"]]
    return {"avg_rest": round(sum(good) / len(good)) if good else None}


def player_profile(athlete):
    """Everything the profile page needs for one athlete (a row from accounts)."""
    sessions = _sessions(athlete["gym_id"], "AND u.id = :uid", uid=athlete["id"])   # newest first
    rest = {r["session_id"]: round(r["avg_rest"]) for r in db.rows(
        """SELECT r.session_id, AVG(r.hr_bpm) AS avg_rest
           FROM rounds r JOIN sessions s ON s.id = r.session_id JOIN users u ON u.card_uid = s.card_uid
           WHERE u.id = :uid AND r.hr_good = 1 AND r.hr_bpm IS NOT NULL
           GROUP BY r.session_id""", uid=athlete["id"])}
    trend = [{"id": s["id"], "t": s["started_at"], "score": s["score"],
              "accuracy": s["accuracy"], "avg_rest": rest.get(s["id"])}
             for s in reversed(sessions)]               # oldest first, for the charts
    return {
        "player": {"id": athlete["player_id"], "nickname": athlete["name"], "card_uid": athlete["card_uid"]},
        "sessions": sessions,
        "trend": trend,
        "best_score": max((s["score"] for s in sessions), default=0),
        "best_streak": max((s["best_streak"] for s in sessions), default=0),
        "accuracy": _acc(sum(s["landed"] for s in sessions), sum(s["thrown"] for s in sessions)),
        "total_sessions": len(sessions),
    }


def delete_sessions(card_uid):
    """Remove every session uploaded for one card (used by "delete my account")."""
    if not card_uid:
        return
    with db.begin() as con:
        db.q(con, "DELETE FROM rounds WHERE session_id IN (SELECT id FROM sessions WHERE card_uid = :c)", c=card_uid)
        db.q(con, "DELETE FROM sessions WHERE card_uid = :c", c=card_uid)


# ---------- fake history so every page has something to show (--sim) ----------
def seed_demo(cards):
    """8 past sessions of 3 rounds for each card. Only runs when there are no sessions."""
    if db.rows("SELECT 1 FROM sessions LIMIT 1"):
        return
    rng = random.Random(7)
    now = time.time()
    for i, card in enumerate(sorted(cards)):
        skill = 0.74 - 0.08 * (i % 4)
        for n in range(8):
            acc = min(0.95, skill + n * 0.012 + rng.uniform(-0.05, 0.05))
            started = now - (8 - n) * 86400 * 1.5 - rng.randint(0, 3600)
            for rn in range(1, 4):
                thrown = rng.randint(14, 24)
                landed = round(thrown * acc)
                hit = landed // 3
                ingest_round({
                    "session": f"demo-{card}-{n}", "round": rn, "card": card, "started_at": started,
                    "score": landed * 10 + hit * 20, "thrown": thrown, "landed": landed,
                    "combos_called": thrown // 3, "combos_hit": hit, "best_streak": rng.randint(1, 3 + n // 2),
                    "hr": {"bpm": rng.randint(134, 150) - n + rn * 3,   # recovery improves with practice
                           "good": rng.random() > 0.1},
                })
