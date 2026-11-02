"""The hub: takes lines from any transport (simulator or BLE), keeps the board
clocks aligned, logs everything and feeds the fusion step.

`run_demo` is a stand-in game loop for testing integration. The real game
engine (rounds, combo generator, scoring) plugs in on top of `Hub`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import protocol as proto
from .clock import ClockSync
from .fusion import FusedPunch, Fuser, ImpactEvent, PunchEvent, VisionEvent

log = logging.getLogger(__name__)

VISION_PORT = 5005
NFC_PORT = 5006


class Hub:
    def __init__(
        self,
        transport,
        fuser: Fuser | None = None,
        log_dir: Path | None = Path("logs"),
        on_punch: Callable[[FusedPunch], None] | None = None,
    ) -> None:
        self.transport = transport
        self.fuser = fuser or Fuser()
        self.clocks: dict[str, ClockSync] = {}
        self.heart_rates: list[tuple[float, int, bool]] = []
        self.player_uid: str | None = None
        self.bad_lines = 0
        self.on_punch = on_punch
        self._clock_replies: dict[str, asyncio.Event] = {}
        self._log = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / time.strftime("session-%Y%m%d-%H%M%S.jsonl")
            self._log = path.open("a", encoding="utf-8")
            log.info("logging to %s", path)

    # -- input

    async def on_line(self, board: str, line: str, t_recv: float) -> None:
        self._write_log({"t": round(t_recv, 4), "board": board, "line": line})
        try:
            if board == "vision":
                self._on_vision(line)
            elif board == "nfc":
                self.player_uid = json.loads(line)["uid"]
                log.info("player signed in: %s", self.player_uid)
            else:
                self._on_board(board, proto.parse_board(line), t_recv)
        except (proto.ProtocolError, ValueError, KeyError, TypeError) as e:
            self.bad_lines += 1
            log.warning("bad line from %s: %r (%s)", board, line, e)

    def _on_board(self, board: str, msg: proto.BoardMessage, t_recv: float) -> None:
        clock = self.clocks.setdefault(board, ClockSync())
        if isinstance(msg, proto.ClockReply):
            clock.reply_received(msg.board_ms, t_recv)
            if board in self._clock_replies:
                self._clock_replies[board].set()
        elif isinstance(msg, proto.Punch):
            t = clock.to_pi_time(msg.board_ms, fallback=t_recv)
            self.fuser.add_punch(PunchEvent(t, msg.hand, msg.type, msg.peak_g))
        elif isinstance(msg, proto.Impact):
            t = clock.to_pi_time(msg.board_ms, fallback=t_recv)
            self.fuser.add_impact(ImpactEvent(t, msg.strength))
        elif isinstance(msg, proto.HeartRate):
            self.heart_rates.append((t_recv, msg.bpm, msg.good))
        # RawWindow lines are already in the session log; training scripts read them from there.

    def _on_vision(self, line: str) -> None:
        d = json.loads(line)
        code = proto.PUNCH_NAMES.get(str(d["type"]).upper(), str(d["type"]).upper())
        self.fuser.add_vision(VisionEvent(float(d["t"]), d["hand"], code, bool(d["contact"]), float(d["conf"])))

    # -- output

    async def send(self, board: str, line: str) -> None:
        self._write_log({"t": round(time.monotonic(), 4), "to": board, "line": line.strip()})
        await self.transport.send(board, line)

    async def sync_clock(self, board: str, pings: int = 5, timeout: float = 1.0) -> ClockSync:
        """Ping a board a few times and keep the best offset estimate."""
        clock = self.clocks.setdefault(board, ClockSync())
        event = self._clock_replies.setdefault(board, asyncio.Event())
        for _ in range(pings):
            event.clear()
            clock.ping_sent(time.monotonic())
            await self.send(board, proto.clock_ping_msg())
            try:
                await asyncio.wait_for(event.wait(), timeout)
            except asyncio.TimeoutError:
                log.warning("no clock reply from %s", board)
            await asyncio.sleep(0.05)
        if clock.synced:
            log.info("%s synced: offset %.3fs, best rtt %.0fms", board, clock.offset, clock.best_rtt * 1000)
        return clock

    def resolve(self, flush: bool = False) -> list[FusedPunch]:
        results = self.fuser.resolve(time.monotonic(), flush=flush)
        for r in results:
            self._write_log({"t": round(r.t, 4), "fused": asdict(r)})
            if self.on_punch:
                self.on_punch(r)
        return results

    def close(self) -> None:
        if self._log:
            self._log.close()
            self._log = None

    def _write_log(self, record: dict) -> None:
        if self._log:
            self._log.write(json.dumps(record) + "\n")
            self._log.flush()


# -------------------------------------------------------------- local inputs


class _UdpLines(asyncio.DatagramProtocol):
    def __init__(self, hub: Hub, source: str) -> None:
        self.hub, self.source = hub, source

    def datagram_received(self, data: bytes, addr) -> None:
        text = data.decode("utf-8", errors="replace").strip()
        asyncio.ensure_future(self.hub.on_line(self.source, text, time.monotonic()))


async def listen_local(hub: Hub, vision_port: int = VISION_PORT, nfc_port: int = NFC_PORT):
    """Receive vision and NFC events from other processes on the Pi via UDP."""
    loop = asyncio.get_running_loop()
    transports = []
    for port, source in ((vision_port, "vision"), (nfc_port, "nfc")):
        t, _ = await loop.create_datagram_endpoint(lambda s=source: _UdpLines(hub, s), local_addr=("127.0.0.1", port))
        transports.append(t)
    return transports


# -------------------------------------------------------------- demo game loop


def score_combo(expected: list[tuple[str, str]], thrown: list[FusedPunch]) -> dict:
    """Compare thrown punches with the called combo, position by position."""
    correct = sum(1 for (h, c), p in zip(expected, thrown) if p.hand == h and p.type == c)
    landed = sum(1 for p in thrown[: len(expected)] if p.landed)
    return {
        "called": len(expected),
        "thrown": len(thrown),
        "correct": correct,
        "landed": landed,
        "points": correct * 10 + landed * 5,
    }


async def run_demo(
    hub: Hub,
    rounds: int = 2,
    combos_per_round: int = 4,
    combo_time: float = 3.0,
    rest_time: float = 4.0,
    stance: str = "orthodox",
    seed: int | None = None,
    printer: Callable[[str], None] = print,
) -> list[dict]:
    """Minimal rounds -> combos -> rest loop to prove the pipeline end to end."""
    rng = random.Random(seed)
    results = []
    for rnd in range(1, rounds + 1):
        printer(f"\n=== Round {rnd} ===")
        await hub.send("bag", proto.mode_msg("ROUND"))
        await hub.send("bag", proto.sound_msg("BELL"))
        for _ in range(combos_per_round):
            combo = [rng.randint(1, 6) for _ in range(rng.randint(2, 4))]
            expected = [proto.expected_punch(n, stance) for n in combo]
            await hub.send("bag", proto.combo_msg(combo))
            await asyncio.sleep(combo_time)
            thrown = hub.resolve(flush=True)
            s = score_combo(expected, thrown)
            s["round"], s["combo"] = rnd, combo
            results.append(s)
            await hub.send("bag", proto.sound_msg("HIT" if s["correct"] == s["called"] else "MISS"))
            calls = " ".join(f"{h}{c}" for h, c in expected)
            got = " ".join(f"{p.hand}{p.type}{'*' if p.landed else ''}" for p in thrown) or "-"
            printer(
                f"called {'-'.join(map(str, combo)):<8} ({calls:<12}) thrown {got:<24} "
                f"correct {s['correct']}/{s['called']}  landed {s['landed']}  +{s['points']}"
            )
        await hub.send("bag", proto.mode_msg("REST"))
        await asyncio.sleep(rest_time)
        if hub.heart_rates:
            _, bpm, good = hub.heart_rates[-1]
            printer(f"rest: heart rate {bpm} bpm{'' if good else ' (unreliable)'}")
    await hub.send("bag", proto.mode_msg("SUMMARY"))
    total = sum(r["points"] for r in results)
    printer(f"\nTotal: {total} points   (* = landed)   unmatched bag impacts: {hub.fuser.unmatched_impacts}")
    return results
