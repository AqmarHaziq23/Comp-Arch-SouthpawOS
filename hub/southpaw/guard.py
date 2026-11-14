"""Guard tracking on the hub: did the boxer keep their hands up during the round?

Johanan's vision process (vision/vision_main.py) checks ~9 times a second whether
each hand is up in guard and sends (UDP 5005):

    {"kind": "guard", "t": 1731.42, "L": true, "R": false}     null = can't tell

A punching hand leaves the guard on purpose, and vision can't tell a punch from a
dropped hand. The hub can: the wrist boards report every punch. So for each punch,
that hand's guard readings from punch_before_s before the peak to punch_after_s after
it are left out. What's left is turned into, per round:

    guard_pct        % of the time the hands were up (both hands together)
    guard_pct_lead   % of the time the lead hand was up (left hand for orthodox)
    guard_pct_rear   % of the time the rear hand was up
    guard_drops      how often a hand stayed down for at least drop_hold_s

The counting matches vision/guard.py (GuardTracker), so the overlay and the round
stats agree apart from the punches. If vision isn't running there are no readings,
and the round simply has no guard stats.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GuardConfig:
    drop_hold_s: float = 0.4     # a hand down this long = one drop (same as vision's config.json)
    max_gap_s: float = 0.5       # longest gap between readings that still counts as time (camera hiccups)
    punch_before_s: float = 0.4  # leave out this long BEFORE a wrist punch's peak (the hand moving out) ...
    punch_after_s: float = 0.6   # ... and this long AFTER it (the hand coming back to guard)
    keep_s: float = 900.0        # forget readings older than this (memory stays small on long sessions)


@dataclass
class _Hand:
    up_s: float = 0.0            # seconds seen up
    known_s: float = 0.0         # seconds we could tell up or down
    drops: int = 0
    down_since: float | None = None
    counted: bool = False        # current down stretch already counted as a drop?


@dataclass
class GuardLog:
    """Keeps vision's guard readings and the wrist punches; works out each round's guard stats."""

    config: GuardConfig = field(default_factory=GuardConfig)
    readings: list[tuple[float, bool | None, bool | None]] = field(default_factory=list)  # (t, L, R)
    punches: list[tuple[float, str]] = field(default_factory=list)                        # (t, hand)

    def add_reading(self, t: float, left: bool | None, right: bool | None) -> None:
        """One vision frame. Times are Pi time (time.monotonic())."""
        self.readings.append((t, left, right))
        self._forget_old(t)

    def add_punch(self, t: float, hand: str) -> None:
        """A wrist-board punch (peak time in Pi time, hand "L"/"R")."""
        self.punches.append((t, hand))

    def _forget_old(self, now: float) -> None:
        cutoff = now - self.config.keep_s
        if self.readings and self.readings[0][0] < cutoff:
            self.readings = [r for r in self.readings if r[0] >= cutoff]
            self.punches = [p for p in self.punches if p[0] >= cutoff]

    def _punching(self, t: float, hand: str) -> bool:
        """Was this hand in the middle of a punch at time t?"""
        c = self.config
        return any(h == hand and p - c.punch_before_s <= t <= p + c.punch_after_s for p, h in self.punches)

    def round_stats(self, t_start: float, t_end: float, stance: str = "orthodox") -> dict | None:
        """Guard stats for the readings between t_start and t_end, or None if vision sent nothing."""
        frames = [r for r in self.readings if t_start <= r[0] <= t_end]
        if not frames:
            return None
        c = self.config
        hands = {"L": _Hand(), "R": _Hand()}
        last_t, last = None, {"L": None, "R": None}

        for t, left, right in frames:
            # Leave out a hand while it's punching (counts as "can't tell")
            punching = {s: self._punching(t, s) for s in "LR"}
            now = {s: (None if punching[s] else up) for s, up in (("L", left), ("R", right))}

            # The time since the previous reading belongs to the PREVIOUS reading's state
            if last_t is not None:
                dt = min(max(0.0, t - last_t), c.max_gap_s)
                for s, up in last.items():
                    if up is not None:
                        hands[s].known_s += dt
                        hands[s].up_s += dt if up else 0.0

            # Drops: down for drop_hold_s, counted once per down stretch; a punch ends the stretch
            for s, up in now.items():
                h = hands[s]
                if up is False:
                    if h.down_since is None:
                        h.down_since, h.counted = t, False
                    if not h.counted and t - h.down_since >= c.drop_hold_s:
                        h.drops += 1
                        h.counted = True
                elif up is True or punching[s]:
                    h.down_since, h.counted = None, False
            last_t, last = t, now

        def pct(up, known):
            return round(100 * up / known) if known > 0 else None

        lead = "L" if stance == "orthodox" else "R"
        rear = "R" if lead == "L" else "L"
        stats = {
            # Both hands together: all the time each hand was up / all the time each hand could be judged.
            # Always between the two single-hand numbers (a hand left out while punching doesn't skew it).
            "guard_pct": pct(hands["L"].up_s + hands["R"].up_s, hands["L"].known_s + hands["R"].known_s),
            "guard_pct_lead": pct(hands[lead].up_s, hands[lead].known_s),
            "guard_pct_rear": pct(hands[rear].up_s, hands[rear].known_s),
            "guard_drops": hands["L"].drops + hands["R"].drops,
        }
        if stats["guard_pct"] is None and stats["guard_pct_lead"] is None and stats["guard_pct_rear"] is None:
            return None  # readings came in, but vision never saw the boxer's hands
        return stats
