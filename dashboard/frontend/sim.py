"""Fake Pi: makes up rounds in exactly the shape the real hub uploads, so every
dashboard page can be built and filmed before the hardware is joined.

  save(round)   one finished round    (the Pi's POST /api/round)
"""
import random
import time

import accounts

# Combo number -> (hand, punch type) for an orthodox boxer, as in protocol/PROTOCOL.md
EXPECTED = {1: ("L", "J"), 2: ("R", "C"), 3: ("L", "H"), 4: ("R", "H"), 5: ("L", "U"), 6: ("R", "U")}
ROUND_SECS = 30


def fake_round(rng, skill=0.72):
    """One round's upload (without session, round and card), played by a boxer who
    throws the right punch and lands it with probability `skill`."""
    combos, t, streak, best, score = [], 1.0, 0, 0, 0
    while True:
        numbers = [rng.randint(1, 6) for _ in range(rng.randint(2, 4))]
        window = 1.2 + 0.6 * len(numbers)
        if t + window > ROUND_SECS:
            break
        punches, correct, at = [], 0, t + rng.uniform(0.3, 0.6)
        for n in numbers:
            hand, kind = EXPECTED[n]
            if rng.random() > skill + 0.15:                 # threw something else
                hand, kind = rng.choice("LR"), rng.choice("JCHU")
            correct += (hand, kind) == EXPECTED[n]
            landed = rng.random() < skill
            g = rng.randint(7, 15) + {"J": 0, "C": 4, "H": 3, "U": 2}[kind]
            punches.append({"hand": hand, "type": kind, "peak_g": g, "t": round(at, 2), "landed": landed,
                            "impact": min(100, g * 4 + rng.randint(-8, 12)) if landed else None})
            at += rng.uniform(0.35, 0.6)
        hit = correct == len(numbers)
        landed_n = sum(x["landed"] for x in punches)
        points = correct * 10 + landed_n * 5 + (10 if hit else 0)   # the hub's scoring
        streak = streak + 1 if hit else 0
        best, score = max(best, streak), score + points
        combos.append({"called": numbers, "hit": hit, "correct": correct, "points": points, "punches": punches})
        t += window + 0.55
    return {"score": score, "best_streak": best, "duration_s": ROUND_SECS,
            "unmatched_impacts": rng.choice([0, 0, 0, 1, 2]), "combos": combos,
            "hr": {"bpm": rng.randint(128, 152), "good": rng.random() > 0.12}}


def seed(save, cards, has_sessions):
    """8 past sessions of 3 rounds for each card, so the history pages have data."""
    if has_sessions:
        return
    rng, now = random.Random(7), time.time()
    for i, card in enumerate(sorted(cards)):
        for n in range(8):
            skill = min(0.92, 0.74 - 0.08 * (i % 4) + n * 0.012 + rng.uniform(-0.04, 0.04))
            started = now - (8 - n) * 86400 * 1.5 - rng.randint(0, 3600)
            for rn in range(1, 4):
                r = fake_round(rng, skill)
                r["hr"]["bpm"] += rn * 3 - n                 # recovery improves with practice
                save(dict(r, session=f"demo-{card}-{n}", round=rn, card=card, started_at=started))


def run(save, speed=1.0):
    """Play sessions forever: a round, a rest, an upload."""
    rng = random.Random()
    sleep = lambda s: time.sleep(s / speed)
    time.sleep(1.5)
    while True:
        cards = accounts.all_cards()
        card = rng.choice(cards) if cards and rng.random() < 0.8 else None   # None: nobody taps = Guest
        session = f"sim-{time.time():.0f}"
        for rn in range(1, 4):
            sleep(20)                                       # the round being played
            save(dict(fake_round(rng, rng.uniform(0.55, 0.85)), session=session, round=rn, card=card))
            sleep(8)                                        # rest
        sleep(12)
