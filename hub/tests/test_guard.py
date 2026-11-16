"""Guard stats on the hub (southpaw/guard.py): vision's hands-up readings, minus punches."""

import asyncio
import json

from southpaw.fusion import Fuser
from southpaw.game import Game, GameConfig
from southpaw.guard import GuardLog
from southpaw.hub import Hub
from southpaw.notify import round_stats
from southpaw.sim import SimConfig, Simulator


def readings(log, start, end, left, right, step=0.1):
    """Add vision readings every `step` s from start to end (end not included)."""
    t = start
    while t < end - 1e-9:
        log.add_reading(round(t, 3), left(t) if callable(left) else left, right(t) if callable(right) else right)
        t += step


def test_hands_up_all_round():
    log = GuardLog()
    readings(log, 0, 10, True, True)
    assert log.round_stats(0, 10) == {"guard_pct": 100, "guard_pct_lead": 100, "guard_pct_rear": 100,
                                      "guard_drops": 0}


def test_a_punch_is_not_a_drop():
    # The right hand is out of guard from 4.9 to 5.5 s, but the wrist board saw a punch at 5.0 s
    log = GuardLog()
    readings(log, 0, 10, True, lambda t: not 4.9 <= t <= 5.5)
    log.add_punch(5.0, "R")
    s = log.round_stats(0, 10)
    assert s["guard_drops"] == 0 and s["guard_pct"] == 100


def test_the_same_dip_without_a_punch_is_a_drop():
    log = GuardLog()
    readings(log, 0, 10, True, lambda t: not 4.9 <= t <= 5.5)
    s = log.round_stats(0, 10)
    assert s["guard_drops"] == 1 and s["guard_pct_rear"] < 100 and s["guard_pct_lead"] == 100


def test_a_punch_only_excuses_its_own_hand():
    # Left hand drops while the RIGHT hand punches: still a drop
    log = GuardLog()
    readings(log, 0, 10, lambda t: not 4.5 <= t <= 6.0, True)
    log.add_punch(5.0, "R")
    assert log.round_stats(0, 10)["guard_drops"] == 1


def test_short_dip_is_not_a_drop():
    log = GuardLog()
    readings(log, 0, 10, lambda t: not 3.0 <= t < 3.3, True)   # 0.3 s < drop_hold_s 0.4
    assert log.round_stats(0, 10)["guard_drops"] == 0


def test_only_the_round_window_counts_and_stance_picks_the_lead_hand():
    log = GuardLog()
    readings(log, 0, 5, False, False)        # before the round: hands down, ignored
    readings(log, 5, 15, False, True)        # the round: left hand down the whole time
    orthodox = log.round_stats(5, 15, "orthodox")
    southpaw = log.round_stats(5, 15, "southpaw")
    assert orthodox["guard_pct_lead"] == 0 and orthodox["guard_pct_rear"] == 100
    assert southpaw["guard_pct_lead"] == 100 and southpaw["guard_pct_rear"] == 0


def test_no_vision_means_no_guard_stats():
    log = GuardLog()
    assert log.round_stats(0, 10) is None
    readings(log, 0, 10, None, None)         # vision running but nobody in view
    assert log.round_stats(0, 10) is None


def test_hub_reads_vision_guard_messages():
    hub = Hub(transport=None, log_dir=None)
    for i in range(20):
        line = json.dumps({"v": 1, "src": "vision", "kind": "guard", "t": i * 0.1, "L": True, "R": i < 10})
        asyncio.run(hub.on_line("vision", line, 0.0))
    s = hub.guard.round_stats(0, 2)
    assert s["guard_pct_lead"] == 100 and s["guard_drops"] == 1 and hub.bad_lines == 0


def play_sim_game(tmp_path, **sim):
    """One simulated game (2 short rounds); returns the round results."""
    async def go():
        simulator = Simulator(SimConfig(seed=4, start_delay=0.6, hr_delay=0.2, **sim))
        hub = Hub(simulator, Fuser(), log_dir=tmp_path)
        game = Game(hub, GameConfig(rounds=2, round_secs=6, rest_secs=0.5, summary_secs=0.1, seed=2),
                    source="sim", printer=lambda s: None)

        async def on_connect(board):
            await hub.sync_clock(board, pings=2)

        runner = asyncio.create_task(simulator.start(hub.on_line, on_connect))
        while not all(hub.clocks.get(b) and hub.clocks[b].synced for b in ("wristL", "wristR", "bag")):
            await asyncio.sleep(0.05)
        rounds = await game.play_one()
        runner.cancel()
        await simulator.stop()
        hub.close()
        return rounds, simulator

    return asyncio.run(go())


def test_sim_game_punches_are_excused_so_a_boxer_who_never_drops_scores_100(tmp_path):
    rounds, _ = play_sim_game(tmp_path, guard_drops_per_min=0)
    for r in rounds:
        assert r.thrown > 0, "the simulated boxer should have punched"
        assert r.guard["guard_drops"] == 0 and r.guard["guard_pct"] == 100, r.guard
        assert round_stats(r, 2, 6)["guard_pct"] == 100   # and it goes into the Telegram stats


def test_sim_game_real_drops_are_counted(tmp_path):
    rounds, sim = play_sim_game(tmp_path, guard_drops_per_min=40)
    assert sum(r.guard["guard_drops"] for r in rounds) >= 1
    assert all(r.guard["guard_pct"] < 100 for r in rounds)


def test_sim_game_without_vision_has_no_guard_stats(tmp_path):
    rounds, _ = play_sim_game(tmp_path, guard=False, vision=False)
    assert all(r.guard is None for r in rounds)
    assert "guard_pct" not in round_stats(rounds[0], 2, 6)
