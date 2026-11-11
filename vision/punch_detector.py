"""
punch_detector.py - Southpaw OS, computer vision (Member D)

Turns body points from the pose model into PUNCH EVENTS:
    "left hook at t=12.48 s, confidence 0.82"

Used in two places, with exactly the same code:
  * live, by vision_main.py (webcam -> pose model -> this -> hub)
  * offline, by evaluate.py (recorded clip landmarks -> this -> compare with labels)

HOW IT WORKS (per arm, independently)
-------------------------------------
At ~9 fps a punch only lasts 2-3 frames, so we don't try to follow a smooth path.
Instead we compare the GUARD pose (before the punch) with the PEAK pose (hand furthest out):

  1. Guard position: while a hand is still, we remember where it sits relative to its
     shoulder (slowly updated, so stepping or leaning doesn't matter).
  2. Start: the hand moves fast (speed), AWAY from its guard position, and is clearly out
     of guard (distance). Pulling a hand back moves it towards guard, so it never triggers.
  3. Peak: the frame where the hand is furthest from its guard position.
  4. End: the hand is back in guard, or has been coming back for 2 frames (or the punch
     takes too long) -> classify and emit the event.

CLASSIFYING (rules on the peak pose; every number is tunable in config.json)
  * Elbow nearly straight (>= straight_min_elbow_deg)          -> STRAIGHT (jab or cross)
  * Otherwise, "uppercut score" (0-1) adds up three signals:
        forearm pointing up + elbow kept low + hand rising into the peak
        score >= uppercut_threshold                           -> UPPERCUT
        score <  uppercut_threshold                           -> HOOK (forearm level, elbow up)
  * Jab vs cross comes from the hand + stance (orthodox: left straight = jab).

The elbow angle uses MediaPipe's 3D "world" points by default. In a flat 2D image a
bent arm can look straight (e.g. a hook seen edge-on from shoulder height), but a
straight arm always looks straight, so 3D is the safer measure.

Distances are in BODY LENGTHS (torso length as seen by the camera), so thresholds work
at any distance from the camera.
"""

import math
from collections import namedtuple

# One body point: x, y in PIXELS (y grows downwards), visibility 0-1,
# and wx, wy, wz = MediaPipe 3D world position in metres (None if not available).
Point = namedtuple("Point", "x y vis wx wy wz")

# The body points we use (MediaPipe's numbering). "L"/"R" = the PERSON's left/right.
POINT_INDEX = {
    "NOSE": 0,
    "L_SHOULDER": 11, "R_SHOULDER": 12,
    "L_ELBOW": 13, "R_ELBOW": 14,
    "L_WRIST": 15, "R_WRIST": 16,
    "L_PINKY": 17, "R_PINKY": 18,
    "L_INDEX": 19, "R_INDEX": 20,
    "L_THUMB": 21, "R_THUMB": 22,
    "L_HIP": 23, "R_HIP": 24,
}


# ---------------------------------------------------------------------------------------
# Getting body points in: from a live MediaPipe result, or from a landmarks CSV row
# ---------------------------------------------------------------------------------------

def pose_from_mediapipe(result, width, height):
    """Convert a MediaPipe PoseLandmarker result into {name: Point}. None if nobody was found."""
    if not result.pose_landmarks:
        return None
    lms = result.pose_landmarks[0]
    world = result.pose_world_landmarks[0] if result.pose_world_landmarks else None
    pose = {}
    for name, idx in POINT_INDEX.items():
        p = lms[idx]
        w = world[idx] if world else None
        pose[name] = Point(p.x * width, p.y * height, p.visibility,
                           w.x if w else None, w.y if w else None, w.z if w else None)
    return pose


def pose_from_csv_row(row, width, height):
    """Convert one row of landmarks.csv (from extract_landmarks.py) into (time, {name: Point} or None)."""
    t = float(row["time_s"])
    if row.get("detected") != "1":
        return t, None

    def num(key):
        value = row.get(key, "")
        return float(value) if value not in ("", None) else None

    pose = {}
    for name in POINT_INDEX:
        x, y = num(f"{name}_x"), num(f"{name}_y")
        if x is None or y is None:
            continue
        pose[name] = Point(x * width, y * height, num(f"{name}_vis") or 0.0,
                           num(f"{name}_wx"), num(f"{name}_wy"), num(f"{name}_wz"))
    return t, pose


# ---------------------------------------------------------------------------------------
# Small geometry helpers
# ---------------------------------------------------------------------------------------

def _dist2(a, b):
    """Distance between two (x, y) pairs."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _angle_at(b, a, c):
    """Angle in degrees at point b, between the lines b->a and b->c. Works for 2D or 3D tuples."""
    v1 = [ai - bi for ai, bi in zip(a, b)]
    v2 = [ci - bi for ci, bi in zip(c, b)]
    n1 = math.sqrt(sum(v * v for v in v1))
    n2 = math.sqrt(sum(v * v for v in v2))
    if n1 == 0 or n2 == 0:
        return None
    cos = sum(x * y for x, y in zip(v1, v2)) / (n1 * n2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _xy(p):
    return (p.x, p.y)


def _world(p):
    if p is None or p.wx is None:
        return None
    return (p.wx, p.wy, p.wz)


def _clip(value, low=0.0, high=1.0):
    return max(low, min(high, value))


class PunchEvent:
    """One detected punch. Times are capture times of frames (seconds, same clock as the input)."""

    def __init__(self, hand, punch_type, kind, conf, t_start, t_peak, t_end, features,
                 peak_points, knuckle_path):
        self.hand = hand                # "L" or "R" (the person's left/right)
        self.type = punch_type          # "jab", "cross", "hook" or "uppercut"
        self.kind = kind                # "straight", "hook" or "uppercut" (before stance mapping)
        self.conf = conf                # 0-1: how sure the rules are
        self.t_start = t_start          # last guard frame before the punch
        self.t_peak = t_peak            # frame where the hand was furthest out (closest to impact)
        self.t_end = t_end              # frame where the punch was recognised as finished
        self.features = features        # the measurements behind the decision (for tuning/logging)
        self.peak_points = peak_points  # {"wrist": (x, y), "knuckle": (x, y) or None} at the peak, pixels
        self.knuckle_path = knuckle_path  # fist positions (pixels) for every frame of the punch

    def as_dict(self):
        return {"hand": self.hand, "type": self.type, "kind": self.kind, "conf": round(self.conf, 3),
                "t_start": self.t_start, "t_peak": self.t_peak, "t_end": self.t_end,
                **{k: v for k, v in self.features.items()}}

    def __repr__(self):
        return (f"PunchEvent({self.hand} {self.type}, conf={self.conf:.2f}, "
                f"peak at {self.t_peak:.2f}s)")


# ---------------------------------------------------------------------------------------
# One arm
# ---------------------------------------------------------------------------------------

class _ArmTracker:
    """Watches one arm (L or R) and reports a punch when one finishes."""

    def __init__(self, side, config):
        self.side = side
        self.cfg_tracker = config["tracker"]
        self.cfg_detect = config["punch_detector"]
        self.cfg_class = config["classifier"]
        self.min_vis = config["pose"]["min_visibility"]
        self.stance = config["player"]["stance"]

        self.rest = None              # guard position: wrist relative to shoulder, in body lengths
                                      # (sideways, up/down[, depth when tracker.motion = "2d+depth"])
        self.prev = None              # previous good frame (dict, see _frame)
        self.active = False           # currently inside a punch?
        self.frames = []              # frames of the current punch (guard frame first)
        self.peak = None              # frame furthest from guard so far
        self.cooldown_until = -math.inf
        self.last_valid_t = None
        self.still_since = None       # when the hand last became still (for re-learning the guard)
        self.frames_coming_back = 0   # consecutive frames the hand has been heading back to guard

    # A snapshot of everything we need from one frame for this arm
    def _frame(self, t, pose, scale):
        s = pose[f"{self.side}_SHOULDER"]
        e = pose[f"{self.side}_ELBOW"]
        w = pose[f"{self.side}_WRIST"]
        k = pose.get(f"{self.side}_INDEX")
        # Wrist relative to shoulder, in body lengths: (sideways, up/down) in the image ...
        rel = ((w.x - s.x) / scale, (w.y - s.y) / scale)
        if self.cfg_tracker.get("motion") == "2d+depth":
            # ... plus towards/away from the camera, from MediaPipe's 3D points (if available)
            if w.wz is not None and s.wz is not None:
                depth = (w.wz - s.wz) / self.cfg_tracker["world_body_length_m"]
            else:
                depth = self.rest[2] if self.rest and len(self.rest) == 3 else 0.0
            rel = rel + (depth,)
        return {
            "t": t, "s": s, "e": e, "w": w, "k": k, "scale": scale,
            "rel": rel,
            "dist": math.dist(rel, self.rest) if self.rest else 0.0,  # how far from the guard position
            "pose": pose,                                              # whole body, for the twin check
        }

    def update(self, t, pose, scale):
        """Feed one frame. Returns a PunchEvent when a punch has just finished, else None."""
        # To FOLLOW the hand we only need the shoulder and wrist. The elbow is only needed to
        # CLASSIFY the punch (checked later), and it's often hidden for a frame on the far arm.
        names = [f"{self.side}_SHOULDER", f"{self.side}_WRIST"]
        valid = (pose is not None and scale and all(n in pose for n in names)
                 and f"{self.side}_ELBOW" in pose
                 and all(pose[n].vis >= self.min_vis for n in names))

        if not valid:
            # Arm not visible. If a punch was in progress and the arm stays lost, finish it.
            if self.active and self.last_valid_t is not None \
                    and (t - self.last_valid_t) * 1000 > self.cfg_detect["max_gap_ms"]:
                return self._finish(t)
            return None

        frame = self._frame(t, pose, scale)
        # Arm dropped (wrist well below the shoulder, e.g. hand hanging by the side between punches)?
        # In image y, which grows downwards, so "below" = larger y.
        frame["low"] = frame["rel"][1] > self.cfg_tracker["hand_low_limit"]

        if self.rest is None:
            # First sighting: assume the hand is in guard - unless it's hanging down
            if frame["low"]:
                self.prev, self.last_valid_t = frame, t
                return None
            self.rest = frame["rel"]
            frame["dist"] = 0.0

        # Speed of the hand, and how fast it moves AWAY from guard (body lengths per second)
        speed, away_speed = 0.0, 0.0
        prev_recent = (self.prev is not None and t > self.prev["t"]
                       and (t - self.prev["t"]) * 1000 <= self.cfg_detect["max_gap_ms"])
        if self.prev is not None and t > self.prev["t"]:
            speed = math.dist(frame["rel"], self.prev["rel"]) / (t - self.prev["t"])
        if prev_recent:
            away_speed = (frame["dist"] - self.prev["dist"]) / (t - self.prev["t"])
        frame["speed"] = speed

        event = None
        if not self.active:
            # A punch starts when the hand moves quickly AWAY from its guard position (its distance
            # from guard grows fast) and is clearly out of guard. Pulling a hand back, or raising a
            # dropped hand, moves it TOWARDS guard, so it never counts. The previous frame must be
            # recent: it becomes the "guard" reference for the punch.
            if (prev_recent and not frame["low"] and t >= self.cooldown_until
                    and away_speed >= self.cfg_detect["trigger_speed"]
                    and frame["dist"] >= self.cfg_detect["trigger_distance"]):
                self._start(self.prev, frame)
            elif speed < self.cfg_tracker["quiet_speed"] and not frame["low"]:
                # Hand is still. Only let the guard position follow it when the hand is near guard,
                # or has been held still somewhere else for a while (the player changed their guard).
                # Otherwise a hand held out after a punch would become "guard", and pulling it back
                # would look like a new punch.
                if self.still_since is None:
                    self.still_since = t
                near_guard = frame["dist"] < self.cfg_detect["trigger_distance"]
                settled = t - self.still_since >= self.cfg_tracker["rest_relearn_s"]
                if near_guard or settled:
                    a = self.cfg_tracker["rest_smoothing"]
                    self.rest = tuple(r + a * (v - r) for r, v in zip(self.rest, frame["rel"]))
            else:
                self.still_since = None
        else:
            d = self.cfg_detect
            peak_dist = self.peak["dist"]
            # Double punch with the same hand (e.g. jab-jab): the hand had clearly started coming
            # back and now shoots out again fast. Finish the first punch and start a new one.
            re_extending = (self.prev["dist"] < peak_dist * d["re_extend_ratio"]
                            and frame["dist"] > self.prev["dist"] and speed >= d["trigger_speed"])
            if frame["low"]:
                # The hand dropped (e.g. arm hanging down after the punch): the punch is over, and
                # this frame is NOT part of it (otherwise a hanging arm would become the "peak")
                event = self._finish(t)
            elif re_extending:
                event = self._finish(self.prev["t"])
                self._start(self.prev, frame)
            else:
                self.frames.append(frame)
                if frame["dist"] > peak_dist:
                    self.peak = frame
                too_long = (t - self.frames[0]["t"]) * 1000 > d["max_duration_ms"]
                # The punch is over when the hand is back in guard, has dropped well back from its
                # furthest point, or has been coming back for 2 frames in a row. (One small step back
                # isn't enough: an uppercut's dip-then-drive can pass near its guard point on screen
                # halfway through the punch.)
                if frame["dist"] < self.peak["dist"] * d["end_ratio"]:
                    self.frames_coming_back += 1
                else:
                    self.frames_coming_back = 0
                back_in_guard = frame["dist"] < d["trigger_distance"]
                clearly_back = frame["dist"] < self.peak["dist"] * d["end_ratio_fast"]
                if back_in_guard or clearly_back or self.frames_coming_back >= 2 or too_long:
                    event = self._finish(t)

        self.prev = frame
        self.last_valid_t = t
        return event

    def flush(self, t):
        """Finish a punch still in progress (e.g. at the end of a clip)."""
        return self._finish(t) if self.active else None

    def _start(self, guard_frame, frame):
        """A punch begins: guard_frame is the last frame before it (our 'before' reference)."""
        self.active = True
        self.frames = [guard_frame, frame]
        self.peak = frame
        self.still_since = None
        self.frames_coming_back = 0

    # -----------------------------------------------------------------------------------
    def _finish(self, t):
        """Punch over: classify it (if it was big enough) and reset."""
        frames, peak = self.frames, self.peak
        self.active, self.frames, self.peak = False, [], None
        self.cooldown_until = t + self.cfg_detect["cooldown_ms"] / 1000.0
        if peak is None or peak["dist"] < self.cfg_detect["min_peak_distance"]:
            return None                                   # too small: a guard adjustment, not a punch
        if self._is_phantom(peak):
            return None                                   # far arm copying the near arm: not a real punch
        return self._classify(frames, peak, t)

    def _is_phantom(self, peak):
        """
        When the far arm is hidden behind the body, MediaPipe often "snaps" its wrist onto the
        NEAR arm, so a jab with the near hand also looks like a punch by the far hand.
        If both wrists are almost in the same place at the peak and THIS is the far arm
        (its shoulder is further from the camera), treat this punch as a phantom.
        """
        limit = self.cfg_detect.get("twin_gap_limit", 0)
        other = "R" if self.side == "L" else "L"
        pose = peak["pose"]
        mine, theirs = peak["w"], pose.get(f"{other}_WRIST")
        my_sh, their_sh = pose.get(f"{self.side}_SHOULDER"), pose.get(f"{other}_SHOULDER")
        if not limit or theirs is None or my_sh is None or their_sh is None:
            return False
        gap = math.hypot(mine.x - theirs.x, mine.y - theirs.y) / peak["scale"]
        if gap >= limit:
            return False
        # Which arm is further from the camera? MediaPipe's depth (bigger z = further away);
        # fall back to "the less visible wrist" if 3D points are missing
        if my_sh.wz is not None and their_sh.wz is not None:
            far = my_sh.wz > their_sh.wz
        else:
            far = mine.vis < theirs.vis
        return far

    def _classify(self, frames, peak, t_end):
        c = self.cfg_class
        # Classify from the peak frame, unless its elbow was hidden: then use the furthest-out
        # frame where the elbow WAS visible (if there is one).
        if peak["e"].vis < self.min_vis:
            seen = [f for f in frames[1:] if f["e"].vis >= self.min_vis]
            if seen:
                peak = max(seen, key=lambda f: f["dist"])
        s, e, w = peak["s"], peak["e"], peak["w"]

        # --- Measurements at the peak -------------------------------------------------------
        elbow_2d = _angle_at(_xy(e), _xy(s), _xy(w))
        elbow_3d = None
        if _world(s) and _world(e) and _world(w):
            elbow_3d = _angle_at(_world(e), _world(s), _world(w))
        use_3d = c["use_world_angles"] and elbow_3d is not None
        elbow = elbow_3d if use_3d else elbow_2d

        # Forearm direction: +1 = wrist straight above elbow, 0 = level, -1 = below.
        # 2D (image) version, and a 3D version that isn't fooled by the camera angle.
        # (Both rely on "up" in the image being up in the room: keep the camera level-ish.)
        forearm_len = _dist2(_xy(w), _xy(e)) or 1.0
        forearm_up_2d = (e.y - w.y) / forearm_len
        forearm_up_3d = None
        if elbow_3d is not None:
            fore_vec = [a - b for a, b in zip(_world(w), _world(e))]
            fore_len = math.sqrt(sum(v * v for v in fore_vec)) or 1.0
            forearm_up_3d = -fore_vec[1] / fore_len           # world y also grows downwards
        forearm_up = forearm_up_3d if (use_3d and forearm_up_3d is not None) else forearm_up_2d

        # Elbow height: + = elbow above the shoulder, - = below (body lengths).
        # Hooks lift the elbow to about shoulder height; uppercuts keep it low, by the ribs.
        elbow_raise = (s.y - e.y) / peak["scale"]

        # How much the hand ROSE into the peak: from its lowest point during the punch
        # (the guard frame or a dip just before an uppercut) up to the peak. Body lengths.
        peak_pos = next(i for i, f in enumerate(frames) if f is peak)
        frames_to_peak = frames[: peak_pos + 1]
        lowest_y = max(f["rel"][1] for f in frames_to_peak)  # y grows downwards, so max = lowest
        lift = lowest_y - peak["rel"][1]

        # Extra measurements, logged for tuning (not used by the rules)
        reach_3d = None
        if elbow_3d is not None:
            upper = math.dist(_world(s), _world(e))
            fore = math.dist(_world(e), _world(w))
            reach_3d = math.dist(_world(s), _world(w)) / (upper + fore) if upper + fore else None
        max_speed = max(f.get("speed", 0.0) for f in frames[1:]) if len(frames) > 1 else 0.0

        # --- Rules ------------------------------------------------------------------------------
        uppercut_score = (c["uppercut_forearm_weight"] * _clip(forearm_up)
                          + c["uppercut_elbow_weight"] * _clip(-elbow_raise / c["uppercut_elbow_drop_full"])
                          + c["uppercut_rise_weight"] * _clip(lift / c["uppercut_rise_full"]))
        if elbow is not None and elbow >= c["straight_min_elbow_deg"]:
            kind = "straight"
            margin = (elbow - c["straight_min_elbow_deg"]) / 20.0
        else:
            kind = "uppercut" if uppercut_score >= c["uppercut_threshold"] else "hook"
            score_margin = abs(uppercut_score - c["uppercut_threshold"]) / 0.25
            elbow_margin = ((c["straight_min_elbow_deg"] - elbow) / 20.0) if elbow is not None else 0.5
            margin = min(score_margin, elbow_margin)

        # Confidence: how clear the decision was x how clearly the arm was seen
        vis = min(s.vis, e.vis, w.vis)
        vis_factor = 0.5 + 0.5 * _clip((vis - self.min_vis) / max(1e-6, 1 - self.min_vis))
        conf = (0.5 + 0.5 * _clip(margin)) * vis_factor

        punch_type = kind if kind != "straight" else self._straight_name()

        features = {
            "elbow_2d": round(elbow_2d, 1) if elbow_2d is not None else None,
            "elbow_3d": round(elbow_3d, 1) if elbow_3d is not None else None,
            "forearm_up_2d": round(forearm_up_2d, 3),
            "forearm_up_3d": round(forearm_up_3d, 3) if forearm_up_3d is not None else None,
            "lift": round(lift, 3),
            "uppercut_score": round(uppercut_score, 3),
            "elbow_raise": round(elbow_raise, 3),
            "reach_3d": round(reach_3d, 3) if reach_3d is not None else None,
            "peak_dist": round(peak["dist"], 3),
            "max_speed": round(max_speed, 2),
            "frames": len(frames),
            "visibility": round(vis, 3),
        }
        peak_points = {"wrist": (w.x, w.y),
                       "knuckle": (peak["k"].x, peak["k"].y) if peak["k"] is not None else None}
        knuckle_path = [((f["k"].x, f["k"].y) if f["k"] is not None else (f["w"].x, f["w"].y))
                        for f in frames]
        return PunchEvent(self.side, punch_type, kind, conf, frames[0]["t"], peak["t"], t_end,
                          features, peak_points, knuckle_path)

    def _straight_name(self):
        """Orthodox: left straight = jab, right = cross. Southpaw: the other way round."""
        lead = "L" if self.stance == "orthodox" else "R"
        return "jab" if self.side == lead else "cross"


# ---------------------------------------------------------------------------------------
# Both arms together
# ---------------------------------------------------------------------------------------

class PunchDetector:
    """
    Feed it one frame at a time:   events = detector.update(t, pose)
      t:    capture time of the frame in seconds (any clock, as long as it only goes forward)
      pose: {name: Point} from pose_from_mediapipe / pose_from_csv_row, or None if nobody was found
    Returns a list of PunchEvents that finished on this frame (usually empty).
    """

    def __init__(self, config):
        self.config = config
        self.arms = {"L": _ArmTracker("L", config), "R": _ArmTracker("R", config)}
        self.scale = None          # body length in pixels (smoothed)
        self.person = False        # was a person visible in the last frame?

    def _update_scale(self, pose):
        """Body length = shoulders-midpoint to hips-midpoint, in pixels (kept steady with smoothing)."""
        need = ("L_SHOULDER", "R_SHOULDER", "L_HIP", "R_HIP")
        if pose is None or not all(n in pose for n in need):
            return
        ls, rs, lh, rh = (pose[n] for n in need)
        mid_s = ((ls.x + rs.x) / 2, (ls.y + rs.y) / 2)
        mid_h = ((lh.x + rh.x) / 2, (lh.y + rh.y) / 2)
        torso = _dist2(mid_s, mid_h)
        if torso < 10:          # nonsense measurement (a few pixels): ignore
            return
        if self.scale is None:
            self.scale = torso
            return
        # Don't let the scale drift while a punch is in progress (the body twists during punches)
        if any(arm.active for arm in self.arms.values()):
            return
        a = self.config["tracker"]["scale_smoothing"]
        # Limit each step to +/-20% so a single bad frame can't throw it off
        target = _clip(torso, self.scale * 0.8, self.scale * 1.2)
        self.scale += a * (target - self.scale)

    def update(self, t, pose):
        self.person = pose is not None
        self._update_scale(pose)
        events = []
        for arm in self.arms.values():
            event = arm.update(t, pose, self.scale)
            if event is not None:
                events.append(event)
        return events

    def flush(self, t):
        """Call at the very end (e.g. end of a clip) to finish punches still in progress."""
        return [e for e in (arm.flush(t) for arm in self.arms.values()) if e is not None]

    def is_busy(self):
        """True while either arm is in the middle of a punch (useful for the overlay)."""
        return any(arm.active for arm in self.arms.values())