"""The hub -> Telegram bot messages (southpaw/notify.py).

A real UDP socket stands in for the bot, so this checks exactly the bytes the
bot would receive."""

import asyncio
import json
import socket

from southpaw.fusion import FusedPunch, Fuser
from southpaw.game import Game, GameConfig, RoundResult, score_combo
from southpaw.hub import Hub
from southpaw.notify import BotNotifier, round_stats
from southpaw.sim import SimConfig, Simulator


def fake_bot():
    """A UDP socket on a free local port, like the bot listening for the hub."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.5)
    return sock, sock.getsockname()[1]


def received(sock):
    """Every message the fake bot got, decoded."""
    out = []
    try:
        while True:
            out.append(json.loads(sock.recv(65535)))
    except socket.timeout:
        return out


def test_round_stats_counts_each_punch_type():
    r = RoundResult(2, hr_bpm=140)
    # 1-2 = jab (left), cross (right); the cross missed the bag
    r.combos.append(score_combo([1, 2], [FusedPunch(1.0, "L", "J", 9, True), FusedPunch(1.3, "R", "C", 9, False)],
                                "orthodox"))
    s = round_stats(r, rounds_total=3, round_secs=30)
    assert s["round"] == 2 and s["rounds"] == 3 and s["duration_s"] == 30
    assert s["thrown"] == 2 and s["landed"] == 1 and s["combos_called"] == 1 and s["combos_hit"] == 1
    assert s["by_type"] == {"jab": {"thrown": 1, "landed": 1}, "cross": {"thrown": 1, "landed": 0}}
    assert s["hr_bpm"] == 140 and s["hr_good"] is True
    json.dumps(s)  # must be plain JSON


def test_guest_rounds_are_not_sent_and_no_bot_is_fine():
    sock, port = fake_bot()
    n = BotNotifier(port=port)
    assert n.send_round(None, {"round": 1}) is False
    assert received(sock) == []
    sock.close()
    # Nobody listening any more: sending must still not raise
    assert n.send_round("04A2B91C", {"round": 1}) is True
    n.close()


def test_full_game_sends_every_round_to_the_bot(tmp_path):
    sock, port = fake_bot()

    async def go():
        sim = Simulator(SimConfig(seed=3, start_delay=0.6, hr_delay=0.2, card="04A2B91C"))
        hub = Hub(sim, Fuser(), log_dir=tmp_path)
        notifier = BotNotifier(port=port)
        cfg = GameConfig(rounds=2, round_secs=5, rest_secs=0.8, summary_secs=0.1, seed=1)
        game = Game(hub, cfg, notifier=notifier, source="sim", printer=lambda s: None)

        async def on_connect(board):
            await hub.sync_clock(board, pings=2)

        runner = asyncio.create_task(sim.start(hub.on_line, on_connect))
        while not all(hub.clocks.get(b) and hub.clocks[b].synced for b in ("wristL", "wristR", "bag")):
            await asyncio.sleep(0.05)
        rounds = await game.play_one()
        runner.cancel()
        await sim.stop()
        notifier.close()
        hub.close()
        return rounds

    rounds = asyncio.run(go())
    msgs = received(sock)
    sock.close()
    assert [m["round"]["round"] for m in msgs] == [1, 2]
    assert all(m["kind"] == "round" and m["uid"] == "04A2B91C" for m in msgs)
    assert [m["round"]["score"] for m in msgs] == [r.score for r in rounds]
    assert all(sum(t["thrown"] for t in m["round"]["by_type"].values()) == m["round"]["thrown"] for m in msgs)
