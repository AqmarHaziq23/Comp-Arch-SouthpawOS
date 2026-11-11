"""Fake Pi: uploads a finished round every so often, like the real Pi does
after every round, so every dashboard page can be built and filmed before the
hardware is joined.

  save(round)   one finished round    (the Pi's POST /api/round)
"""
import random
import time

import accounts


def run(save, speed=1.0):
    rng = random.Random()
    sleep = lambda s: time.sleep(s / speed)
    time.sleep(1.5)
    while True:
        cards = accounts.all_cards()
        card = rng.choice(cards) if cards and rng.random() < 0.8 else None   # None: nobody taps = Guest
        session = f"sim-{time.time():.0f}"
        streak = 0
        for rn in range(1, 4):
            sleep(20)                                   # the round being played
            called = rng.randint(5, 8)
            thrown = landed = hit = best = 0
            for _ in range(called):
                results = [rng.random() < 0.72 for _ in range(3)]
                thrown += 3
                landed += sum(results)
                streak = streak + 1 if all(results) else 0
                hit += all(results)
                best = max(best, streak)
            save({"session": session, "round": rn, "card": card, "score": landed * 10 + hit * 20,
                  "thrown": thrown, "landed": landed, "combos_called": called, "combos_hit": hit,
                  "best_streak": best, "hr": {"bpm": rng.randint(128, 152), "good": rng.random() > 0.12}})
            sleep(8)                                    # rest
        sleep(12)
