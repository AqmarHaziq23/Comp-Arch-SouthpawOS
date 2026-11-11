"""Runs the whole pipeline (simulator -> clock sync -> fusion -> scoring)."""

import asyncio

from southpaw.fusion import Fuser
from southpaw.hub import Hub, run_demo
from southpaw.sim import SimConfig, Simulator


def test_demo_round_with_simulator(tmp_path):
    async def go():
        sim = Simulator(SimConfig(seed=1, hit_rate=1.0, type_accuracy=1.0, hr_delay=0.2))
        hub = Hub(sim, Fuser(), log_dir=tmp_path)

        async def on_connect(board):
            await hub.sync_clock(board, pings=3)

        runner = asyncio.create_task(sim.start(hub.on_line, on_connect))
        await asyncio.sleep(0)
        while not all(hub.clocks.get(b) and hub.clocks[b].synced for b in ("wristL", "wristR", "bag", "hr")):
            await asyncio.sleep(0.05)
        results = await run_demo(
            hub, rounds=1, combos_per_round=2, combo_time=2.5, rest_time=0.5, seed=3, printer=lambda s: None
        )
        runner.cancel()
        await sim.stop()
        hub.close()
        return hub, results

    hub, results = asyncio.run(go())
    assert len(results) == 2
    for r in results:
        # Perfect simulated player: every punch thrown correctly and landed.
        assert r["correct"] == r["called"] == r["thrown"]
        assert r["landed"] == r["called"]
    assert hub.heart_rates, "rest period should produce a heart-rate reading"
    assert hub.bad_lines == 0
    assert list(tmp_path.glob("session-*.jsonl"))
