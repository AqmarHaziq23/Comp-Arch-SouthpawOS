import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from southpaw.fusion import FusedPunch, Fuser
from southpaw.game import Game, GameConfig, RoundResult, score_combo
from southpaw.hub import Hub
from southpaw.sim import SimConfig, Simulator

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "dashboard" / "backend"))
from pi_uploader import Uploader  # noqa: E402


def fp(hand, type_, landed=True, t=1.0):
    return FusedPunch(t=t, hand=hand, type=type_, peak_g=9, landed=landed)


def test_unknown_type_counts_if_the_hand_is_right():
    # 1-2 = left jab, right cross (orthodox)
    c = score_combo([1, 2], [fp("L", "X"), fp("R", "X", landed=False)], "orthodox")
    assert c.correct == 2 and c.hit and c.landed == 1
    assert c.points == 2 * 10 + 1 * 5 + 10


def test_wrong_hand_or_wrong_known_type_misses():
    c = score_combo([1, 2], [fp("R", "X"), fp("R", "H")], "orthodox")
    assert c.correct == 0 and not c.hit


def test_round_totals_and_streak():
    r = RoundResult(1)
    for hit in (True, True, False, True):
        r.combos.append(score_combo([1], [fp("L", "J" if hit else "C")], "orthodox"))
    assert r.combos_hit == 3 and r.best_streak == 2 and r.thrown == 4 and r.landed == 4


# ---------- a stand-in for Jovis's website: same token check and validation as store.ingest_round ----------

class FakeWebsite(BaseHTTPRequestHandler):
    rounds: list = []
    token = "test-token"

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/api/round" or self.headers.get("Authorization") != f"Bearer {self.token}":
            return self._reply(401, {"error": "token"})
        for p in body if isinstance(body, list) else [body]:
            ok = (
                isinstance(p.get("session"), str) and p["session"]
                and isinstance(p.get("round"), int) and 1 <= p["round"] <= 99
                and all(isinstance(p.get(k), int) and p[k] >= 0 for k in ("score", "thrown", "landed"))
                and p["landed"] <= p["thrown"]
                and all(p.get(k) is None or (isinstance(p[k], int) and p[k] >= 0)
                        for k in ("combos_called", "combos_hit", "best_streak"))
                and (p.get("hr") is None or 20 <= p["hr"]["bpm"] <= 250)
            )
            if not ok:
                return self._reply(400, {"error": f"bad round {p}"})
            FakeWebsite.rounds.append(p)
        self._reply(200, {"ok": True})

    def _reply(self, code, data):
        raw = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def website():
    FakeWebsite.rounds = []
    server = HTTPServer(("127.0.0.1", 0), FakeWebsite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_full_game_with_card_uploads_every_round(tmp_path, website):
    from southpaw.db import Store

    async def go():
        sim = Simulator(SimConfig(seed=2, hit_rate=1.0, start_delay=0.6, hr_delay=0.2))
        hub = Hub(sim, Fuser(), log_dir=tmp_path)
        store = Store(":memory:")
        up = Uploader(website, FakeWebsite.token, queue_file=str(tmp_path / "unsent.jsonl"))
        cfg = GameConfig(rounds=2, round_secs=5, rest_secs=0.8, summary_secs=0.1, seed=1)
        game = Game(hub, cfg, store=store, uploader=up, source="sim", printer=lambda s: None)

        async def on_connect(board):
            await hub.sync_clock(board, pings=2)

        runner = asyncio.create_task(sim.start(hub.on_line, on_connect))
        while not all(hub.clocks.get(b) and hub.clocks[b].synced for b in ("wristL", "wristR", "bag")):
            await asyncio.sleep(0.05)

        async def tap_card():  # player taps their card while the bag shows the start screen
            while game.state != "wait":
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)
            await hub.on_line("nfc", '{"uid": "04A2B91C"}', time.monotonic())

        tap = asyncio.create_task(tap_card())
        rounds = await game.play_one()
        await tap
        runner.cancel()
        await sim.stop()
        hub.close()
        return rounds, store

    rounds, store = asyncio.run(go())
    assert len(rounds) == 2 and all(r.combos for r in rounds)
    up = FakeWebsite.rounds
    assert [r["round"] for r in up] == [1, 2]
    assert all(r["card"] == "04A2B91C" for r in up)
    assert len({r["session"] for r in up}) == 1
    assert [r["score"] for r in up] == [r.score for r in rounds]
    assert any("hr" in r for r in up), "rest heart rate should be uploaded"
    assert not (tmp_path / "unsent.jsonl").exists(), "nothing should be left waiting"
    (row,) = store.conn.execute("SELECT id FROM sessions").fetchall()
    assert store.session_summary(row["id"])["player"] == "Player B91C"


def test_start_hits_outside_wait_are_ignored(tmp_path):
    sim = Simulator(SimConfig(seed=1))
    hub = Hub(sim, Fuser(), log_dir=tmp_path)
    game = Game(hub, GameConfig(), printer=lambda s: None)
    game.state = "round"
    for _ in range(10):
        game._on_impact(time.monotonic(), 50)
    assert not game._started.is_set()
    game.state = "wait"
    for _ in range(4):
        game._on_impact(time.monotonic(), 50)
    assert not game._started.is_set()
    game._on_impact(time.monotonic(), 50)
    assert game._started.is_set()
    hub.close()


def test_vision_status_and_null_contact_are_handled(tmp_path):
    hub = Hub(Simulator(), Fuser(), log_dir=tmp_path)

    async def go():
        await hub.on_line("vision", '{"v":1,"src":"vision","kind":"status","t":1.0,"fps":8.9}', 1.0)
        await hub.on_line("vision", '{"v":1,"kind":"punch","t":2.0,"hand":"L","type":"hook","contact":null,"conf":0.8}', 2.0)

    asyncio.run(go())
    assert hub.bad_lines == 0 and hub.vision_status_at is not None
    assert hub.fuser._vision[0].type == "H" and hub.fuser._vision[0].contact is False
    hub.close()
