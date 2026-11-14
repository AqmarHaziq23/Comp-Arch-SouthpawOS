"""Fake boards (and a fake camera), so the hub, dashboard and vision code can be
built and tested without hardware.

The simulator behaves like the real BLE transport: it delivers protocol lines
through on_line(board, line, t_recv) and accepts Pi commands through send().
When the Pi sends a combo to the bag, a simulated player throws it.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import protocol as proto

OnLine = Callable[[str, str, float], Awaitable[None]]
BOARDS = ("wristL", "wristR", "bag")


@dataclass
class SimConfig:
    stance: str = "orthodox"
    hit_rate: float = 0.8  # chance each punch hits the bag
    type_accuracy: float = 0.9  # chance the wrist board classifies correctly
    vision: bool = True
    vision_accuracy: float = 0.75
    reaction: tuple[float, float] = (0.35, 0.7)  # s before the first punch
    gap: tuple[float, float] = (0.25, 0.45)  # s between punches
    ble_latency: tuple[float, float] = (0.010, 0.045)  # s, one way
    wrist_delay: tuple[float, float] = (0.25, 0.31)  # s, wrist waits for samples after the peak
    vision_latency: tuple[float, float] = (0.12, 0.30)  # s
    rest_bpm: tuple[int, int] = (150, 175)  # first rest; drops each round
    start_delay: float = 1.0  # s after M,WAIT before the simulated player hits the bag 5 times
    hr_delay: float = 2.0  # s from M,REST until the player has a finger on the pulse sensor
    card: str | None = None  # NFC card the simulated player taps before each game (None = guest)
    # Guard tracking (vision "guard" messages, ~9 a second): hands are up, except the punching
    # hand during each punch, and now and then a real drop
    guard: bool = True
    guard_rate: float = 9.0  # guard messages per second (the Pi's camera rate)
    guard_drops_per_min: float = 4.0  # how often the simulated boxer drops a hand
    guard_drop_secs: tuple[float, float] = (0.6, 1.5)  # how long a drop lasts
    seed: int | None = None


class Simulator:
    def __init__(self, config: SimConfig | None = None) -> None:
        self.cfg = config or SimConfig()
        self.rng = random.Random(self.cfg.seed)
        # Each board booted at a different time, so their millis() differ.
        now = time.monotonic()
        self._boot = {b: now - self.rng.uniform(5, 120) for b in BOARDS}
        self._on_line: OnLine | None = None
        self._tasks: set[asyncio.Task] = set()
        self._rests = 0
        self.connected = asyncio.Event()
        # Guard: until when each hand is out of guard (punching / dropped). A separate random
        # generator, so turning guard on/off doesn't change the punches a given seed produces.
        self._guard_rng = random.Random(None if self.cfg.seed is None else self.cfg.seed + 1)
        self._punch_until = {"L": 0.0, "R": 0.0}
        self._drop_until = {"L": 0.0, "R": 0.0}
        self.guard_drops = 0  # real drops the simulated boxer made (for tests)

    def board_ms(self, board: str, t: float | None = None) -> int:
        t = time.monotonic() if t is None else t
        return int((t - self._boot[board]) * 1000)

    async def start(self, on_line: OnLine, on_connect=None) -> None:
        self._on_line = on_line
        self.connected.set()
        if on_connect:
            for b in BOARDS:
                await on_connect(b)
        if self.cfg.guard:
            self._later(0, self._guard_loop())
        await asyncio.Event().wait()  # run until cancelled, like the BLE transport

    async def send(self, board: str, line: str) -> None:
        line = line.strip()
        if line == "T":
            self._later(self._lat(), self._clock_reply(board))
        elif board == "bag" and line.startswith("C,"):
            self._later(0, self._throw(proto.parse_combo(line)))
        elif board == "bag" and line == "M,WAIT":
            if self.cfg.card:  # tap the card first, like a real player at the start screen
                tap = json.dumps({"uid": self.cfg.card})
                self._later(self.cfg.start_delay / 2, self._deliver("nfc", tap, 0))
            self._later(self.cfg.start_delay, self._start_hits())
        elif board == "bag" and line == "M,REST":
            self._later(self.cfg.hr_delay, self._heart_rate())
        # S,* and other M,* commands only change sound/screen on a real board.

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()

    # -- internals

    def _lat(self) -> float:
        return self.rng.uniform(*self.cfg.ble_latency)

    def _later(self, delay: float, coro) -> None:
        async def run():
            if delay:
                await asyncio.sleep(delay)
            await coro

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, board: str, line: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._on_line:
            await self._on_line(board, line, time.monotonic())

    async def _clock_reply(self, board: str) -> None:
        # The ping reaches the board now; the reply takes another hop back.
        await self._deliver(board, f"T,{self.board_ms(board)}", self._lat())

    async def _throw(self, numbers: list[int]) -> None:
        cfg = self.cfg
        await asyncio.sleep(self.rng.uniform(*cfg.reaction))
        for n in numbers:
            hand, code = proto.expected_punch(n, cfg.stance)
            t_punch = time.monotonic()
            self._punch_until[hand] = t_punch + 0.35  # the punching hand is out of guard until it's back
            wrist = "wristL" if hand == "L" else "wristR"

            sent = code
            if self.rng.random() > cfg.type_accuracy:
                sent = self.rng.choice([c for c in proto.PUNCH_CODES if c != code])
            peak = self.rng.randint(6, 16)
            self._later(
                0,
                self._deliver(
                    wrist,
                    f"P,{hand},{sent},{self.board_ms(wrist, t_punch)},{peak}",
                    self.rng.uniform(*cfg.wrist_delay) + self._lat(),
                ),
            )

            hit = self.rng.random() < cfg.hit_rate
            if hit:
                t_hit = t_punch + self.rng.uniform(0.015, 0.06)
                strength = min(100, peak * 6 + self.rng.randint(-10, 10))
                self._later(
                    t_hit - t_punch,
                    self._deliver("bag", f"I,{self.board_ms('bag', t_hit)},{strength}", self._lat()),
                )

            if cfg.vision:
                v_type = code if self.rng.random() < cfg.vision_accuracy else self.rng.choice(list(proto.PUNCH_CODES))
                event = {
                    "t": round(t_punch + self.rng.uniform(-0.05, 0.05), 3),
                    "hand": hand,
                    "type": v_type,
                    "contact": hit if self.rng.random() < 0.85 else not hit,
                    "conf": round(self.rng.uniform(0.5, 0.95), 2),
                }
                self._later(0, self._deliver("vision", json.dumps(event), self.rng.uniform(*cfg.vision_latency)))

            await asyncio.sleep(self.rng.uniform(*cfg.gap))

    async def _guard_loop(self) -> None:
        """Pretend to be vision's guard check: one {"kind": "guard"} message per camera frame."""
        cfg, rng = self.cfg, self._guard_rng
        frame = 1.0 / cfg.guard_rate
        while True:
            now = time.monotonic()
            for hand in "LR":
                # Now and then the boxer lets a hand drop for a while (a real drop)
                if now >= self._drop_until[hand] and rng.random() < cfg.guard_drops_per_min / 60 * frame:
                    self._drop_until[hand] = now + rng.uniform(*cfg.guard_drop_secs)
                    self.guard_drops += 1
            up = {h: now >= self._punch_until[h] and now >= self._drop_until[h] for h in "LR"}
            msg = {"v": 1, "src": "vision", "kind": "guard", "t": round(now, 3), "L": up["L"], "R": up["R"]}
            self._later(0, self._deliver("vision", json.dumps(msg), rng.uniform(0.1, 0.15)))
            await asyncio.sleep(frame)

    async def _start_hits(self, n: int = 5) -> None:
        """The player hits the bag 5 times to start a game."""
        for _ in range(n):
            t = time.monotonic()
            self._later(0, self._deliver("bag", f"I,{self.board_ms('bag', t)},{self.rng.randint(40, 80)}", self._lat()))
            await asyncio.sleep(self.rng.uniform(0.3, 0.6))

    async def _heart_rate(self) -> None:
        lo, hi = self.cfg.rest_bpm
        bpm = self.rng.randint(lo, hi) - 8 * self._rests  # recovers a bit each round
        self._rests += 1
        good = "1" if self.rng.random() < 0.85 else "0"
        await self._deliver("bag", f"H,{max(bpm, 70)},{good}", self._lat())
