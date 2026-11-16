"""Sessions and rounds: what the Pi uploads, and what the pages read.

The Pi posts one JSON object after every round (ingest_round). A round carries
the NFC card ID that was tapped; a session belongs to whichever athlete account
has that card. Every read below is limited to one gym in the SQL itself.

A round can also carry the detail behind its totals, taken from the protocol
messages the Pi handled (protocol/PROTOCOL.md): every combo it called (C) and
whether that was a HIT or a MISS (S), every punch the wrists reported (P), and
the bag impact (I) that backed each landed punch.
"""
import time

import db

PUNCH_NAMES = {"J": "Jab", "C": "Cross", "H": "Hook", "U": "Uppercut", "X": "Unknown"}
PUNCH_WORDS = {"JAB": "J", "CROSS": "C", "HOOK": "H", "UPPERCUT": "U", "UNKNOWN": "X"}
COMBO_NAMES = {1: "Jab", 2: "Cross", 3: "Lead hook", 4: "Rear hook", 5: "Lead uppercut", 6: "Rear uppercut"}
HAND_NAMES = {"L": "Left", "R": "Right"}


def _acc(landed, thrown):
    return round(100.0 * landed / thrown) if thrown else 0


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


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


def _opt(p, key, **limits):
    return None if p.get(key) is None else _int(p, key, **limits)


def _num(p, key, low, high):
    v = p.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
        raise ValueError(f'"{key}" must be a number from {low} to {high}.')
    return float(v)


def _combos(p):
    """Check and tidy the optional "combos" list. Returns [] when there is none."""
    raw = p.get("combos")
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 200:
        raise ValueError('"combos" must be a list of at most 200 combos.')
    out = []
    for c in raw:
        if not isinstance(c, dict):
            raise ValueError('Each combo must be an object like {"called": [1, 2, 3], "hit": true, "punches": [...]}.')
        called = c.get("called")
        if isinstance(called, str):
            called = [int(x) if x.strip().isdigit() else x for x in called.split("-")]
        if (not isinstance(called, list) or not 1 <= len(called) <= 8
                or any(isinstance(n, bool) or n not in COMBO_NAMES for n in called)):
            raise ValueError('A combo\'s "called" must be 1 to 8 combo numbers from 1 to 6, like [1, 2, 3] or "1-2-3".')
        thrown = c.get("punches") or []
        if not isinstance(thrown, list) or len(thrown) > 40:
            raise ValueError('A combo\'s "punches" must be a list of at most 40 punches.')
        punches = []
        for x in thrown:
            if not isinstance(x, dict):
                raise ValueError('Each punch must be an object like {"hand": "L", "type": "J", "landed": true}.')
            hand, kind = str(x.get("hand", "")).upper(), str(x.get("type", "")).upper()
            kind = PUNCH_WORDS.get(kind, kind)
            if hand not in HAND_NAMES or kind not in PUNCH_NAMES:
                raise ValueError('A punch needs "hand" (L or R) and "type" (J, C, H, U or X).')
            impact = x.get("impact", x.get("impact_strength"))
            t = x.get("t")      # a time that makes no sense is left out, not a reason to lose the round
            t = float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) and -5 <= t <= 3600 else None
            punches.append({"hand": hand, "type": kind, "landed": int(bool(x.get("landed"))),
                            "peak_g": _opt(x, "peak_g", high=100), "t": t,
                            "impact": _opt({"impact": impact}, "impact", high=100)})
        out.append({"called": "-".join(str(int(n)) for n in called), "hit": int(bool(c.get("hit"))),
                    "correct": _opt(c, "correct", high=40), "points": _opt(c, "points"), "punches": punches})
    return out


def ingest_round(p):
    """Save one finished round. Returns the session id. Raises ValueError for bad data.

    {"session": "pi-20261111-153000",   required: the Pi's name for this session, same for all its rounds
     "round": 1,                        required: 1, 2, 3...
     "card": "04A2B91C",                NFC card ID; leave out for a guest
     "score": 120, "thrown": 18, "landed": 14,
     "combos_called": 6, "combos_hit": 2, "best_streak": 3,      optional
     "hr": {"bpm": 132, "good": true},                           optional: rest reading after the round (H)
     "duration_s": 30, "unmatched_impacts": 1,                   optional
     "started_at": 1794379269.0,                                 optional: session start, unix seconds
     "combos": [                                                 optional: the detail behind the totals
       {"called": [1, 2, 3], "hit": true, "correct": 3, "points": 55,          C,1-2-3 then S,HIT
        "punches": [{"hand": "L", "type": "J", "peak_g": 11, "t": 1.42,       P,L,J,48213,11
                     "landed": true, "impact": 72}, ...]}]}                    I,48260,72

    thrown, landed, combos_called and combos_hit are counted from "combos" when left out.
    Sending the same session + round again replaces it, so the Pi can retry safely."""
    if not isinstance(p, dict):
        raise ValueError("Send one JSON object per round.")
    key = " ".join(str(p.get("session") or "").split())[:64]
    if not key:
        raise ValueError('"session" is required.')
    card = "".join(str(p.get("card") or "").split()).upper()[:32] or None
    round_no = _int(p, "round", low=1, high=99)
    combos = _combos(p)
    every = [x for c in combos for x in c["punches"]]
    score = _int(p, "score", 0)
    thrown, landed = _int(p, "thrown", len(every)), _int(p, "landed", sum(x["landed"] for x in every))
    if landed > thrown:
        raise ValueError('"landed" cannot be more than "thrown".')
    called = _int(p, "combos_called", len(combos)) if combos else _opt(p, "combos_called")
    hit = _int(p, "combos_hit", sum(c["hit"] for c in combos)) if combos else _opt(p, "combos_hit")
    hr = p.get("hr") if isinstance(p.get("hr"), dict) else {}
    bpm = _int(hr, "bpm", low=20, high=250) if hr.get("bpm") is not None else None
    now = time.time()
    started = p.get("started_at")
    started = float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else now
    old = "(SELECT id FROM rounds WHERE session_id = :s AND round_no = :n)"
    with db.begin() as con:
        found = db.q(con, "SELECT id, card_uid FROM sessions WHERE session_key = :k", k=key)
        if found:
            sid = found[0]["id"]
            db.q(con, "UPDATE sessions SET updated_at = :t, card_uid = :c WHERE id = :i",
                 t=now, c=found[0]["card_uid"] or card, i=sid)
        else:
            sid = db.q(con, """INSERT INTO sessions (session_key, card_uid, started_at, updated_at)
                               VALUES (:k, :c, :s, :t) RETURNING id""", k=key, c=card, s=started, t=now)[0]["id"]
        for table in ("punches", "combos"):
            db.q(con, f"DELETE FROM {table} WHERE round_id IN {old}", s=sid, n=round_no)
        db.q(con, "DELETE FROM rounds WHERE session_id = :s AND round_no = :n", s=sid, n=round_no)
        rid = db.q(con,
                   """INSERT INTO rounds (session_id, round_no, score, thrown, landed, combos_called, combos_hit,
                                          best_streak, hr_bpm, hr_good, received_at, duration_s, unmatched_impacts)
                      VALUES (:s, :n, :score, :thrown, :landed, :cc, :ch, :streak, :bpm, :good, :t, :dur, :un)
                      RETURNING id""",
                   s=sid, n=round_no, score=score, thrown=thrown, landed=landed, cc=called, ch=hit,
                   streak=_opt(p, "best_streak"), bpm=bpm,
                   good=None if bpm is None else int(bool(hr.get("good", True))), t=now,
                   dur=_num(p, "duration_s", 1, 3600), un=_opt(p, "unmatched_impacts"))[0]["id"]
        seq = 0
        for i, c in enumerate(combos, 1):
            db.q(con, """INSERT INTO combos (round_id, seq, called, correct, hit, points)
                         VALUES (:r, :i, :called, :correct, :hit, :points)""",
                 r=rid, i=i, called=c["called"], correct=c["correct"], hit=c["hit"], points=c["points"])
            for x in c["punches"]:
                seq += 1
                db.q(con, """INSERT INTO punches (round_id, combo_seq, seq, t, hand, type, peak_g, landed, impact)
                             VALUES (:r, :c, :seq, :t, :hand, :type, :peak_g, :landed, :impact)""",
                     r=rid, c=i, seq=seq, **x)
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
_ROUND_COLS = """r.id, r.session_id, r.round_no, r.score, r.thrown, r.landed, r.combos_called, r.combos_hit,
                 r.best_streak, r.hr_bpm, r.hr_good, r.received_at, r.duration_s, r.unmatched_impacts"""


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
    rounds = db.rows(f"SELECT {_ROUND_COLS} FROM rounds r WHERE r.session_id = :s ORDER BY r.round_no", s=sid)
    for r in rounds:
        r["accuracy"] = _acc(r["landed"], r["thrown"])
    hrs = [{"round_no": r["round_no"], "bpm": r["hr_bpm"], "good": r["hr_good"]}
           for r in rounds if r["hr_bpm"] is not None]
    return {"session": found[0], "rounds": rounds, "hr": hrs}


def _round_view(r, session):
    """Add everything the round dashboard shows to one row of `rounds`."""
    punches = db.rows("""SELECT combo_seq, seq, t, hand, type, peak_g, landed, impact
                         FROM punches WHERE round_id = :r ORDER BY seq""", r=r["id"])
    combos = db.rows("SELECT seq, called, correct, hit, points FROM combos WHERE round_id = :r ORDER BY seq", r=r["id"])
    for x in punches:
        x["name"], x["hand_name"] = PUNCH_NAMES[x["type"]], HAND_NAMES[x["hand"]]
    for c in combos:
        c["numbers"] = [int(n) for n in c["called"].split("-")]
        c["names"] = [COMBO_NAMES[n] for n in c["numbers"]]
        c["punches"] = [x for x in punches if x["combo_seq"] == c["seq"]]
        c["landed"] = sum(x["landed"] for x in c["punches"])

    def group(key, names):
        out = []
        for code, name in names.items():
            mine = [x for x in punches if x[key] == code]
            if mine:
                hits = sum(x["landed"] for x in mine)
                out.append({"code": code, "name": name, "thrown": len(mine), "landed": hits,
                            "missed": len(mine) - hits, "accuracy": _acc(hits, len(mine)),
                            "avg_g": _avg(x["peak_g"] for x in mine),
                            "avg_impact": _avg(x["impact"] for x in mine if x["landed"])})
        return out

    gs = [x["peak_g"] for x in punches if x["peak_g"] is not None]
    r.update(
        accuracy=_acc(r["landed"], r["thrown"]), session=session, nickname=session["nickname"],
        player_id=session["player_id"], has_detail=bool(combos), combos=combos, punches=punches,
        by_type=group("type", PUNCH_NAMES), by_hand=group("hand", HAND_NAMES),
        avg_g=_avg(gs), max_g=max(gs, default=None),
        avg_impact=_avg(x["impact"] for x in punches if x["landed"]),
        per_min=round(r["thrown"] * 60 / r["duration_s"]) if r["duration_s"] else None)
    return r


def round_detail(sid, round_no, gym_id):
    """One round with its combos and punches, or None if it is not one of this gym's."""
    session = _sessions(gym_id, "AND s.id = :sid", sid=sid)
    found = db.rows(f"SELECT {_ROUND_COLS} FROM rounds r WHERE r.session_id = :s AND r.round_no = :n",
                    s=sid, n=round_no)
    return _round_view(found[0], session[0]) if session and found else None


def latest_round(gym_id):
    """The round uploaded most recently for one of this gym's athletes, or None.
    This is what the "Latest round" page shows."""
    found = db.rows(
        f"""SELECT {_ROUND_COLS}
            FROM rounds r JOIN sessions s ON s.id = r.session_id JOIN users u ON u.card_uid = s.card_uid
            WHERE u.gym_id = :gym
            ORDER BY r.received_at DESC, r.id DESC LIMIT 1""", gym=gym_id)
    if not found:
        return None
    return _round_view(found[0], _sessions(gym_id, "AND s.id = :sid", sid=found[0]["session_id"])[0])


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
    mine = "(SELECT id FROM sessions WHERE card_uid = :c)"
    with db.begin() as con:
        for table in ("punches", "combos"):
            db.q(con, f"DELETE FROM {table} WHERE round_id IN (SELECT id FROM rounds WHERE session_id IN {mine})",
                 c=card_uid)
        db.q(con, f"DELETE FROM rounds WHERE session_id IN {mine}", c=card_uid)
        db.q(con, "DELETE FROM sessions WHERE card_uid = :c", c=card_uid)
