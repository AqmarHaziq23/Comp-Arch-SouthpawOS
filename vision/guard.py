"""
guard.py - Southpaw OS, computer vision (Member D)

Is the boxer keeping their guard up?

1. GuardChecker looks at ONE camera frame and decides, for each hand:
       up = True    fist is up by the face (in guard)
       up = False   fist has dropped (or is out away from the face)
       up = None    can't tell (hand or face hidden, nobody in view)

   The rule (all numbers are in config.json, section "guard"):
     * the FIST (average of the wrist and knuckle points) must be no lower than a line a
       little below the mouth (about the bottom of the chin):
         - rear hand: up to rear_max_below_mouth below the mouth
         - lead hand: up to lead_max_below_mouth below the mouth
         (the same by default; lower lead_max_below_mouth to demand a higher lead hand)
     * and sideways no further from the nose than max_side_reach (a hand stretched out
       to the side at head height isn't a guard, and neither is a punch)

   Distances are measured in "body lengths" (shoulders to hips as the camera sees it),
   so the same numbers work whether the boxer stands near or far from the camera.

2. GuardTracker adds frames up over time:
     * guard %   = share of the time each hand (and both together) was up
     * drops     = how often a hand stayed down for at least drop_hold_s
                   (short dips, like the moment between two punches, don't count)

   Punches themselves are NOT judged here: a punching hand leaves the guard on purpose.
   The hub knows when the wrist boards saw a punch, so it passes those moments in as
   "ignore" (see GuardTracker.update). Run without the hub, vision counts everything.

This file has no OpenCV/MediaPipe imports, so test_guard.py can test it anywhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class HandGuard:
    """The guard check for one hand in one frame."""
    up: bool | None                      # True = in guard, False = dropped, None = can't tell
    margin: float | None = None          # body lengths ABOVE (+) or BELOW (-) this hand's limit line
    fist: tuple[float, float] | None = None   # fist position in pixels (for the overlay)
    limit_y: float | None = None         # pixel height of this hand's limit line (for the overlay)


def _visible(pose, names, min_vis):
    """True if every named point exists and MediaPipe thinks it's visible."""
    return all(n in pose and pose[n].vis >= min_vis for n in names)


class GuardChecker:
    """Decides, frame by frame, whether each hand is up in guard."""

    def __init__(self, config):
        self.cfg = config["guard"]
        self.min_vis = config["pose"]["min_visibility"]
        # Orthodox boxers lead with the left hand (jab with the left); southpaws with the right
        self.lead = "L" if config["player"]["stance"] == "orthodox" else "R"
        self.scale = None                # body length in pixels, smoothed over frames
        self.scale_is_guess = False      # True while scale is only the rough guess from the shoulders

    # ---------------------------------------------------------------------------------
    def _update_scale(self, pose):
        """Body length in pixels: shoulder midpoint to hip midpoint, smoothed so it doesn't jitter.
        If the hips are out of view, the last good value is kept."""
        torso = ["L_SHOULDER", "R_SHOULDER", "L_HIP", "R_HIP"]
        if _visible(pose, torso, self.min_vis):
            sx = (pose["L_SHOULDER"].x + pose["R_SHOULDER"].x) / 2
            sy = (pose["L_SHOULDER"].y + pose["R_SHOULDER"].y) / 2
            hx = (pose["L_HIP"].x + pose["R_HIP"].x) / 2
            hy = (pose["L_HIP"].y + pose["R_HIP"].y) / 2
            length = math.hypot(hx - sx, hy - sy)
            if length > 1:
                a = self.cfg["scale_smoothing"]
                if self.scale is None or self.scale_is_guess:
                    # First real measurement: use it straight away instead of slowly
                    # smoothing away from the guess (the guess was 2x off in testing)
                    self.scale, self.scale_is_guess = length, False
                else:
                    self.scale += a * (length - self.scale)
        elif self.scale is None and _visible(pose, ["L_SHOULDER", "R_SHOULDER"], self.min_vis):
            # Never seen the hips yet (camera too close?): rough guess from the shoulder width
            width = math.hypot(pose["L_SHOULDER"].x - pose["R_SHOULDER"].x,
                               pose["L_SHOULDER"].y - pose["R_SHOULDER"].y)
            if width > 1:
                self.scale = width * self.cfg["shoulder_to_body_length"]
                self.scale_is_guess = True
        return self.scale

    def _face(self, pose, scale):
        """(x, mouth_y) of the face in pixels, or None if the face can't be seen."""
        nose_ok = _visible(pose, ["NOSE"], self.min_vis)
        if _visible(pose, ["MOUTH_L", "MOUTH_R"], self.min_vis):
            mouth_x = (pose["MOUTH_L"].x + pose["MOUTH_R"].x) / 2
            mouth_y = (pose["MOUTH_L"].y + pose["MOUTH_R"].y) / 2
            return (pose["NOSE"].x if nose_ok else mouth_x), mouth_y
        if nose_ok:   # mouth hidden: it sits about 0.1 body lengths below the nose
            return pose["NOSE"].x, pose["NOSE"].y + 0.1 * scale
        return None

    def _fist(self, pose, side):
        """Fist position: the wrist, averaged with whichever knuckle points are visible.
        None if the wrist itself can't be seen."""
        if not _visible(pose, [f"{side}_WRIST"], self.min_vis):
            return None
        points = [pose[f"{side}_{n}"] for n in ("WRIST", "INDEX", "PINKY")
                  if _visible(pose, [f"{side}_{n}"], self.min_vis)]
        return (sum(p.x for p in points) / len(points), sum(p.y for p in points) / len(points))

    # ---------------------------------------------------------------------------------
    def check(self, pose):
        """{"L": HandGuard, "R": HandGuard} for one frame. pose = {name: Point} or None."""
        unknown = {side: HandGuard(None) for side in "LR"}
        if pose is None:
            return unknown
        scale = self._update_scale(pose)
        if not scale:
            return unknown
        face = self._face(pose, scale)
        if face is None:
            return unknown
        face_x, mouth_y = face

        result = {}
        for side in "LR":
            fist = self._fist(pose, side)
            below = self.cfg["lead_max_below_mouth"] if side == self.lead else self.cfg["rear_max_below_mouth"]
            limit_y = mouth_y + below * scale          # y grows downwards: a bigger y is LOWER
            if fist is None:
                result[side] = HandGuard(None, limit_y=limit_y)
                continue
            margin = (limit_y - fist[1]) / scale       # + = fist above the line, - = below
            close_to_face = abs(fist[0] - face_x) / scale <= self.cfg["max_side_reach"]
            result[side] = HandGuard(margin >= 0 and close_to_face, margin, fist, limit_y)
        return result


# =====================================================================================
@dataclass
class _HandStats:
    up_s: float = 0.0                  # seconds this hand was seen up
    known_s: float = 0.0               # seconds we could tell either way
    drops: int = 0
    down_since: float | None = None    # when the current "down" stretch started
    counted: bool = False              # has the current down stretch been counted as a drop?


@dataclass
class GuardTracker:
    """Adds up guard frames over time: guard % and number of drops, per hand and overall."""
    drop_hold_s: float = 0.4           # a hand must stay down this long to count as a drop
    max_gap_s: float = 0.5             # longest gap between frames that still counts as time
    hands: dict = field(default_factory=lambda: {"L": _HandStats(), "R": _HandStats()})
    both_up_s: float = 0.0             # time with BOTH hands up
    both_known_s: float = 0.0          # time we could see both hands
    _last_t: float | None = None
    _last: dict = field(default_factory=dict)

    def update(self, t, states, ignore=()):
        """Add one frame.
        t:      frame time in seconds (time.monotonic())
        states: {"L": True/False/None, "R": ...} from GuardChecker (HandGuard.up)
        ignore: hands to leave out of this frame, e.g. ("R",) while the right hand is punching
        Returns the hands that have JUST become a drop (were down for drop_hold_s), e.g. ["L"]."""
        states = {s: (None if s in ignore else states.get(s)) for s in "LR"}

        # 1. The time since the previous frame belongs to the PREVIOUS frame's state
        if self._last_t is not None:
            dt = min(max(0.0, t - self._last_t), self.max_gap_s)   # a long gap (camera hiccup) counts as max_gap_s
            for s, up in self._last.items():
                if up is not None:
                    self.hands[s].known_s += dt
                    self.hands[s].up_s += dt if up else 0.0
            if self._last["L"] is not None and self._last["R"] is not None:
                self.both_known_s += dt
                self.both_up_s += dt if (self._last["L"] and self._last["R"]) else 0.0

        # 2. Drops: a hand that has been down for drop_hold_s, counted once per down stretch
        new_drops = []
        for s, up in states.items():
            h = self.hands[s]
            if up is False:
                if h.down_since is None:
                    h.down_since, h.counted = t, False
                if not h.counted and t - h.down_since >= self.drop_hold_s:
                    h.drops += 1
                    h.counted = True
                    new_drops.append(s)
            elif up is True or s in ignore:      # back in guard, or punching: the down stretch is over
                h.down_since, h.counted = None, False
            # up is None (can't see): keep the current stretch going, it may still be a drop

        self._last_t, self._last = t, states
        return new_drops

    def is_dropped(self, side):
        """True while this hand is in a counted drop (down for at least drop_hold_s)."""
        return self.hands[side].counted

    def pct(self, side=None):
        """Guard % for one hand ("L"/"R"), or for both hands together (None). None if never seen."""
        up, known = ((self.both_up_s, self.both_known_s) if side is None
                     else (self.hands[side].up_s, self.hands[side].known_s))
        return 100.0 * up / known if known > 0 else None

    def summary(self, lead="L"):
        """Round stats for the hub / Telegram, e.g. {"guard_pct": 82, "guard_drops": 3, ...}."""
        rear = "R" if lead == "L" else "L"

        def rounded(x):
            return None if x is None else round(x)

        return {
            "guard_pct": rounded(self.pct()),                 # both hands up
            "guard_drops": self.hands["L"].drops + self.hands["R"].drops,
            "guard_pct_lead": rounded(self.pct(lead)),
            "guard_pct_rear": rounded(self.pct(rear)),
        }
