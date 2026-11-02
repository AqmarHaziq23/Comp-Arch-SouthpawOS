"""Decide whether each punch landed, by matching wrist punches with bag impacts
(and, when the camera is on, vision events). All times are Pi seconds.

Rules (tune on real hardware):
- A punch LANDED if a bag impact falls within IMPACT_WINDOW of it. Punches are
  matched in time order, each to its closest free impact, and each impact can
  back only one punch.
- Vision is a second opinion. It never decides "landed" on its own, so the game
  works the same with the camera off. Its punch type and contact are recorded
  next to the wrist's so you can see how often they agree.
- A punch is only resolved once its window has fully passed, so late events
  (vision especially) still get a chance to match.
"""

from __future__ import annotations

from dataclasses import dataclass, field

IMPACT_WINDOW = 0.150  # s, wrist punch <-> bag impact
VISION_WINDOW = 0.300  # s, wrist punch <-> vision frame (camera is slower)
VISION_LATENCY = 0.400  # s, how long to wait for vision events to arrive
STALE_IMPACT = 1.0  # s, unmatched impacts older than this are dropped


@dataclass(frozen=True)
class PunchEvent:
    t: float
    hand: str
    type: str
    peak_g: int


@dataclass(frozen=True)
class ImpactEvent:
    t: float
    strength: int


@dataclass(frozen=True)
class VisionEvent:
    t: float
    hand: str
    type: str
    contact: bool
    conf: float


@dataclass(frozen=True)
class FusedPunch:
    t: float
    hand: str
    type: str  # the wrist's classification (primary)
    peak_g: int
    landed: bool
    impact_strength: int | None = None
    impact_dt: float | None = None  # impact time minus punch time
    vision_type: str | None = None
    vision_contact: bool | None = None
    vision_conf: float | None = None

    @property
    def vision_agrees(self) -> bool | None:
        return None if self.vision_type is None else self.vision_type == self.type


@dataclass
class Fuser:
    use_vision: bool = True
    impact_window: float = IMPACT_WINDOW
    vision_window: float = VISION_WINDOW
    vision_latency: float = VISION_LATENCY
    unmatched_impacts: int = 0
    _punches: list[PunchEvent] = field(default_factory=list)
    _impacts: list[ImpactEvent] = field(default_factory=list)
    _vision: list[VisionEvent] = field(default_factory=list)

    def add_punch(self, e: PunchEvent) -> None:
        self._punches.append(e)

    def add_impact(self, e: ImpactEvent) -> None:
        self._impacts.append(e)

    def add_vision(self, e: VisionEvent) -> None:
        if self.use_vision:
            self._vision.append(e)

    def _wait(self) -> float:
        w = self.impact_window
        if self.use_vision:
            w = max(w, self.vision_window + self.vision_latency)
        return w

    def resolve(self, now: float, flush: bool = False) -> list[FusedPunch]:
        """Return punches whose matching window has closed (all of them if flush)."""
        cutoff = float("inf") if flush else now - self._wait()
        ready = sorted((p for p in self._punches if p.t <= cutoff), key=lambda p: p.t)
        self._punches = [p for p in self._punches if p.t > cutoff]

        out = []
        for p in ready:
            impact = self._take_closest(self._impacts, p.t, self.impact_window)
            vision = self._take_closest(
                [v for v in self._vision if v.hand == p.hand], p.t, self.vision_window
            )
            if vision is not None:
                self._vision.remove(vision)
            out.append(
                FusedPunch(
                    t=p.t,
                    hand=p.hand,
                    type=p.type,
                    peak_g=p.peak_g,
                    landed=impact is not None,
                    impact_strength=impact.strength if impact else None,
                    impact_dt=(impact.t - p.t) if impact else None,
                    vision_type=vision.type if vision else None,
                    vision_contact=vision.contact if vision else None,
                    vision_conf=vision.conf if vision else None,
                )
            )

        # Drop old events nobody claimed (bag swinging, missed wrist detections).
        stale = (now if not flush else float("inf")) - STALE_IMPACT
        before = len(self._impacts)
        self._impacts = [i for i in self._impacts if i.t > stale]
        self.unmatched_impacts += before - len(self._impacts)
        self._vision = [v for v in self._vision if v.t > stale]
        return out

    def _take_closest(self, events: list, t: float, window: float):
        best = None
        for e in events:
            dt = abs(e.t - t)
            if dt <= window and (best is None or dt < abs(best.t - t)):
                best = e
        if best is not None and best in self._impacts:
            self._impacts.remove(best)
        return best
