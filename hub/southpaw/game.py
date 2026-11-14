"""The SouthpawOS game, as planned on the whiteboard:

    1. boards and Pi on               (hub connected to the boards)
    2. player taps their NFC card     (optional: no card = guest)
    3. player hits the bag 5 times    -> game starts
    4. rounds of called combos, with a rest between rounds (heart rate)
    5. every round goes to the website (Jovis's pi_uploader), the local database
       and the Telegram bot (notify.py), which messages the card's owner
    6. summary on the bag screen, then back to step 2

Only the hub decides what happens in the game. The boards just report punches,
hits and heart rate, and the bag shows what the hub tells it to (M,WAIT /
M,ROUND / C,... / M,REST / M,SUMMARY).
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as proto
from .fusion import FusedPunch
from .hub import Hub

log = logging.getLogger(__name__)


@dataclass
class GameConfig:
    rounds: int = 3
    round_secs: float = 30.0
    rest_secs: float = 20.0
    summary_secs: float = 10.0
    start_hits: int = 5            # bag hits that start a game
    start_window: float = 6.0      # ...within this many seconds
    stance: str = "orthodox"
    min_combo: int = 2
    max_combo: int = 4
    combo_base_secs: float = 1.2   # time allowed for a combo: base + per punch
    combo_per_punch: float = 0.6
    warn_secs: float = 10.0        # "10 seconds left" sound
    seed: int | None = None


@dataclass
class ComboResult:
    numbers: list[int]
    punches: list[FusedPunch]
    correct: int
    landed: int
    hit: bool                      # every called punch thrown correctly
    points: int


@dataclass
class RoundResult:
    number: int
    combos: list[ComboResult] = field(default_factory=list)
    hr_bpm: int | None = None
    hr_good: bool = True

    @property
    def thrown(self) -> int:
        return sum(len(c.punches) for c in self.combos)

    @property
    def landed(self) -> int:
        return sum(1 for c in self.combos for p in c.punches if p.landed)

    @property
    def score(self) -> int:
        return sum(c.points for c in self.combos)

    @property
    def combos_hit(self) -> int:
        return sum(1 for c in self.combos if c.hit)

    @property
    def best_streak(self) -> int:
        best = run = 0
        for c in self.combos:
            run = run + 1 if c.hit else 0
            best = max(best, run)
        return best


def punch_matches(p: FusedPunch, hand: str, code: str) -> bool:
    """Right hand, and right type -- or type unknown (X) until the wrist classifier exists."""
    return p.hand == hand and (p.type == code or p.type == proto.UNKNOWN)


def score_combo(numbers: list[int], punches: list[FusedPunch], stance: str) -> ComboResult:
    expected = [proto.expected_punch(n, stance) for n in numbers]
    correct = sum(1 for (h, c), p in zip(expected, punches) if punch_matches(p, h, c))
    landed = sum(1 for p in punches[: len(expected)] if p.landed)
    hit = correct == len(expected)
    points = correct * 10 + landed * 5 + (10 if hit else 0)
    return ComboResult(numbers, punches, correct, landed, hit, points)


class Game:
    def __init__(
        self,
        hub: Hub,
        config: GameConfig | None = None,
        store=None,             # db.Store, optional
        uploader=None,          # dashboard/backend/pi_uploader.Uploader, optional
        notifier=None,          # notify.BotNotifier (Telegram bot), optional
        source: str = "ble",
        printer: Callable[[str], None] = print,
    ) -> None:
        self.hub = hub
        self.cfg = config or GameConfig()
        self.store = store
        self.uploader = uploader
        self.notifier = notifier
        self.source = source
        self.say = printer
        self.rng = random.Random(self.cfg.seed)
        self.state = "idle"
        self._start_hits: list[float] = []
        self._started = asyncio.Event()
        hub.on_impact = self._on_impact

    # -- start trigger: N bag hits within the window, only while waiting

    def _on_impact(self, t: float, strength: int) -> None:
        if self.state != "wait":
            return
        now = time.monotonic()
        self._start_hits = [x for x in self._start_hits if now - x <= self.cfg.start_window] + [now]
        if len(self._start_hits) >= self.cfg.start_hits:
            self._started.set()

    async def wait_for_start(self) -> None:
        self.state = "wait"
        self._start_hits.clear()
        self._started.clear()
        self.hub.player_uid = None  # the next card tap picks the player
        await self.hub.send("bag", proto.mode_msg("WAIT"))
        self.say(f"\nWaiting: tap a card (optional), then hit the bag {self.cfg.start_hits}x to start")
        await self._started.wait()

    # -- one game

    async def play_one(self) -> list[RoundResult]:
        await self.wait_for_start()
        player = self.hub.player_uid
        self.say(f"Game on! player: {player or 'guest'}")
        self.hub.fuser.clear()  # the start hits aren't punches
        session_id = self.store.start_session(player, self.source) if self.store else None
        if self.uploader:
            await asyncio.to_thread(self.uploader.start_session, player)

        rounds = []
        for n in range(1, self.cfg.rounds + 1):
            r = await self._play_round(n)
            if self.store:
                self.store.add_punches(session_id, n, [p for c in r.combos for p in c.punches])
            await self._rest(r, last=(n == self.cfg.rounds))
            if self.store and r.hr_bpm is not None:
                self.store.add_heart_rate(session_id, n, r.hr_bpm, r.hr_good)
            rounds.append(r)
            await self._upload(r)
            self._notify(player, r)

        total = sum(r.score for r in rounds)
        if self.store:
            self.store.end_session(session_id, total)
        if self.uploader:
            await asyncio.to_thread(self.uploader.end_session)
        self.state = "summary"
        await self.hub.send("bag", proto.mode_msg("SUMMARY"))
        thrown = sum(r.thrown for r in rounds)
        landed = sum(r.landed for r in rounds)
        self.say(f"\n=== Game over: {total} points, {landed}/{thrown} landed ===")
        await asyncio.sleep(self.cfg.summary_secs)
        return rounds

    async def run_forever(self, games: int | None = None) -> None:
        played = 0
        while games is None or played < games:
            await self.play_one()
            played += 1

    # -- rounds

    async def _play_round(self, n: int) -> RoundResult:
        self.state = "round"
        r = RoundResult(n)
        await self.hub.send("bag", proto.mode_msg("ROUND"))
        await self.hub.send("bag", proto.sound_msg("BELL"))
        self.say(f"\n--- Round {n}/{self.cfg.rounds} ---")
        end = time.monotonic() + self.cfg.round_secs
        warned = False
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            if not warned and left <= self.cfg.warn_secs and self.cfg.round_secs > self.cfg.warn_secs:
                await self.hub.send("bag", proto.sound_msg("WARN"))
                warned = True
            numbers = [self.rng.randint(1, 6) for _ in range(self.rng.randint(self.cfg.min_combo, self.cfg.max_combo))]
            window = self.cfg.combo_base_secs + self.cfg.combo_per_punch * len(numbers)
            if window > left + 0.5:
                break  # not enough time for another combo
            self.hub.resolve(flush=True)  # drop anything thrown between combos
            await self.hub.send("bag", proto.combo_msg(numbers))
            await asyncio.sleep(window)
            # Wrist messages arrive ~0.3 s after the punch, so wait for them before scoring.
            await asyncio.sleep(self.hub.fuser.wrist_latency + self.hub.fuser.impact_window)
            c = score_combo(numbers, self.hub.resolve(flush=True), self.cfg.stance)
            r.combos.append(c)
            await self.hub.send("bag", proto.sound_msg("HIT" if c.hit else "MISS"))
            calls = " ".join(f"{h}{t}" for h, t in (proto.expected_punch(x, self.cfg.stance) for x in numbers))
            got = " ".join(f"{p.hand}{p.type}{'*' if p.landed else ''}" for p in c.punches) or "-"
            self.say(f"  {'-'.join(map(str, numbers)):<8} ({calls:<12}) thrown {got:<22} +{c.points}")
        await self.hub.send("bag", proto.sound_msg("BELL"))
        self.say(f"Round {n}: {r.score} points, {r.landed}/{r.thrown} landed, "
                 f"{r.combos_hit}/{len(r.combos)} combos, best streak {r.best_streak}")
        return r

    async def _rest(self, r: RoundResult, last: bool) -> None:
        self.state = "rest"
        await self.hub.send("bag", proto.mode_msg("REST"))
        before = len(self.hub.heart_rates)
        await asyncio.sleep(self.cfg.rest_secs if not last else min(self.cfg.rest_secs, 5.0))
        readings = self.hub.heart_rates[before:]
        good = [x for x in readings if x[2]]
        pick = (good or readings)[-1] if readings else None
        if pick:
            r.hr_bpm, r.hr_good = pick[1], pick[2]
            self.say(f"Rest: heart rate {r.hr_bpm} bpm{'' if r.hr_good else ' (unreliable)'}")

    async def _upload(self, r: RoundResult) -> None:
        if not self.uploader:
            return
        ok = await asyncio.to_thread(
            self.uploader.send_round,
            r.number,
            score=r.score,
            thrown=r.thrown,
            landed=r.landed,
            combos_called=len(r.combos),
            combos_hit=r.combos_hit,
            best_streak=r.best_streak,
            hr_bpm=r.hr_bpm,
            hr_good=r.hr_good,
        )
        self.say(f"Round {r.number} {'uploaded to the website' if ok else 'kept on the Pi (website not reachable)'}")

    def _notify(self, player: str | None, r: RoundResult) -> None:
        """Send the round to the Telegram bot (it messages the card's owner). Guests are skipped."""
        if not self.notifier or not player:
            return
        from .notify import round_stats

        self.notifier.send_round(player, round_stats(r, self.cfg.rounds, self.cfg.round_secs))
