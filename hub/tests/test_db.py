import asyncio

import pytest

from southpaw.db import GUEST, Store
from southpaw.fusion import FusedPunch, Fuser
from southpaw.hub import Hub, run_demo
from southpaw.sim import SimConfig, Simulator


def punch(t, hand="L", type_="J", landed=True):
    return FusedPunch(t=t, hand=hand, type=type_, peak_g=9, landed=landed, impact_strength=60 if landed else None)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_session_summary_counts_punches_and_heart_rate(store):
    store.add_player("04A2B91C", "Yue Ming")
    sid = store.start_session("04A2B91C", "ble")
    store.add_punches(sid, 1, [punch(1.0), punch(1.4, "R", "C", landed=False), punch(1.8, type_="X")])
    store.add_heart_rate(sid, 1, 158, True)
    store.end_session(sid, 45)

    s = store.session_summary(sid)
    assert s["player"] == "Yue Ming" and s["total_score"] == 45
    assert s["thrown"] == 3 and s["landed"] == 2
    assert {r["type"]: r["thrown"] for r in s["by_type"]} == {"C": 1, "J": 1, "X": 1}
    assert s["heart_rates"] == [{"round": 1, "bpm": 158, "good": 1}]


def test_unknown_card_is_registered_and_guest_exists(store):
    assert store.player(GUEST)["name"] == "Guest"
    sid = store.start_session("DEADBEEF", "ble")
    assert store.player("DEADBEEF")["name"] == "Player BEEF"
    store.add_player("DEADBEEF", "Haziq", "southpaw")  # renaming keeps the history
    store.end_session(sid, 10)
    assert store.leaderboard()[0]["name"] == "Haziq"


def test_leaderboard_uses_best_session_and_hides_sim_runs(store):
    store.add_player("A", "Alpha")
    store.add_player("B", "Bravo")
    for uid, score, source in (("A", 30, "ble"), ("A", 80, "ble"), ("B", 50, "ble"), ("B", 999, "sim")):
        store.end_session(store.start_session(uid, source), score)
    store.start_session("B", "ble")  # unfinished sessions don't count

    rows = store.leaderboard()
    assert [(r["name"], r["best"], r["sessions"]) for r in rows] == [("Alpha", 80, 2), ("Bravo", 50, 1)]
    assert store.leaderboard(include_sim=True)[0]["best"] == 999


def test_demo_saves_session_to_database(tmp_path, store):
    async def go():
        sim = Simulator(SimConfig(seed=5, hit_rate=1.0, type_accuracy=1.0, hr_delay=0.2))
        hub = Hub(sim, Fuser(), log_dir=tmp_path)

        async def on_connect(board):
            await hub.sync_clock(board, pings=2)

        runner = asyncio.create_task(sim.start(hub.on_line, on_connect))
        while not all(hub.clocks.get(b) and hub.clocks[b].synced for b in ("wristL", "wristR", "bag")):
            await asyncio.sleep(0.05)
        results = await run_demo(
            hub, rounds=1, combos_per_round=2, combo_time=2.5, rest_time=0.6, seed=1,
            printer=lambda s: None, store=store, source="sim",
        )
        runner.cancel()
        await sim.stop()
        hub.close()
        return results

    results = asyncio.run(go())
    (row,) = store.conn.execute("SELECT id FROM sessions").fetchall()
    s = store.session_summary(row["id"])
    assert s["source"] == "sim" and s["player"] == "Guest"
    assert s["total_score"] == sum(r["points"] for r in results)
    assert s["thrown"] == sum(r["thrown"] for r in results)
    assert len(s["heart_rates"]) == 1
