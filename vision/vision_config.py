"""
vision_config.py - Southpaw OS, computer vision (Member D)

One place for every tunable setting of the vision module.

* DEFAULTS below holds a sensible value for every setting, so the code always runs,
  even if config.json is missing or older than the code.
* config.json (same sections and names) overrides any of these values. Tune there, not in code.
* Scripts also accept   --set section.name=value   to try a value once without editing files,
  e.g.  python evaluate.py recordings/test_left --set classifier.straight_min_elbow_deg=140

Distances are in "body lengths" (the length of the torso, shoulders to hips, as seen
by the camera), so the same thresholds work whether the player stands near or far.
"""

import copy
import json
import os

DEFAULTS = {
    "camera": {
        "source": "0",            # webcam number (0 = /dev/video0), a /dev/v4l/by-id/... path, or a video file
        "width": 640,
        "height": 480,
        "fps": 30,
    },
    "pose": {
        "model_path": "pose_landmarker_lite.task",
        "min_detection_confidence": 0.5,
        "min_presence_confidence": 0.5,
        "min_tracking_confidence": 0.5,
        "min_visibility": 0.5,    # ignore body points MediaPipe thinks are hidden
    },
    "player": {
        "stance": "orthodox",     # orthodox: left hand leads (left straight = jab). southpaw: the reverse.
    },
    "tracker": {
        "scale_smoothing": 0.05,  # how fast the body-size estimate adapts (0-1, small = steady)
        "rest_smoothing": 0.25,   # how fast each hand's "guard position" adapts while the hand is still
        "quiet_speed": 1.0,       # below this hand speed (body lengths/s) the hand counts as resting in guard
        "rest_relearn_s": 1.0,    # a hand held still AWAY from guard this long becomes the new guard position
        "hand_low_limit": 0.55,   # wrist more than this far BELOW the shoulder (body lengths) = arm dropped:
                                  # not a punch, not guard (ends a punch; never used as guard position)
        "motion": "2d",           # how hand movement is measured:
                                  #   "2d"       = in the image only (least noisy)
                                  #   "2d+depth" = image + depth from MediaPipe's 3D points; catches punches
                                  #                that move towards/away from the camera, but depth is noisier
        "world_body_length_m": 0.5,  # typical torso length in metres, to put depth in body lengths
    },
    "punch_detector": {
        "trigger_speed": 1.2,     # a punch starts when the hand moves AWAY from guard faster than this (body lengths/s) ...
        "trigger_distance": 0.15, # ... and is at least this far from its guard position (body lengths)
        "min_peak_distance": 0.25,  # ignore movements that never get this far from guard (guard adjustments);
                                    # uppercuts are the shortest punches, so this mostly matters for them
        "end_ratio": 0.75,        # hand back below this fraction of its furthest point for 2 frames = punch over
        "end_ratio_fast": 0.55,   # ...or below this fraction for just 1 frame (clearly coming back; less delay)
        "re_extend_ratio": 0.0,   # same hand shoots out again after coming back below this fraction = a new
                                  # punch (for jab-jab doubles). 0 = off until tested on real doubles; try 0.75
        "max_duration_ms": 700,   # a single punch never takes longer than this
        "max_gap_ms": 300,        # if the arm is lost for longer than this mid-punch, give up on that punch
        "cooldown_ms": 200,       # after a punch ends, ignore that hand for this long
        "twin_gap_limit": 0.15,   # both wrists closer than this (body lengths) at the peak = the hidden far
                                  # arm is copying the near arm (a MediaPipe quirk): drop the far arm's punch.
                                  # 0 = off
    },
    "classifier": {
        "use_world_angles": True,       # angles from MediaPipe's 3D world points (camera-angle independent)
        "straight_min_elbow_deg": 120,  # elbow at least this straight at the furthest point = jab/cross
                                        # (at ~9 fps the frame at full extension is often missed, so this
                                        # is well below 180; hooks/uppercuts sit around 50-100)
        # Hook vs uppercut: uppercut score (0-1) = sum of three signals, each 0-1, times its weight
        "uppercut_forearm_weight": 0.45,  # forearm pointing up at the peak
        "uppercut_elbow_weight": 0.35,    # elbow kept low (hooks lift the elbow to shoulder height)
        "uppercut_rise_weight": 0.2,      # hand rose into the peak
        "uppercut_elbow_drop_full": 0.4,  # elbow this far below the shoulder (body lengths) = fully "low"
        "uppercut_rise_full": 0.3,        # a rise of this many body lengths = full rise
        "uppercut_threshold": 0.5,        # score at or above this = uppercut, below = hook
        "min_confidence_to_send": 0.3,    # punches below this confidence are not sent to the hub
    },
    "contact": {
        "calibration_file": "calibration.local.json",  # bag corners, made by calibrate.py (git-ignored)
        "use_knuckle_point": True,      # use the index-knuckle point (the fist) rather than the wrist
        "margin_px": 10,                # fist this close to the bag outline still counts as contact
    },
    "output": {
        "hub_host": "127.0.0.1",        # where the hub listens (Member C). Use the Pi's IP if vision runs on a laptop.
        "hub_port": 5005,
        "heartbeat_interval_s": 1.0,    # "vision is alive" message this often
        "enabled": True,
    },
    "debug": {
        "show_window": True,            # needs a desktop (Raspberry Pi Connect); turn off over SSH
        "record_path": None,            # save the overlay video here (None = don't)
        "trail_length": 15,
        "print_events": True,
    },
}


def _merge(base, override):
    """Copy values from override into base, section by section (unknown names are kept too)."""
    for key, value in override.items():
        if key.startswith("_"):          # "_comment" entries in config.json are notes, not settings
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _parse_value(text):
    """Turn a --set value string into a number/bool/None when it looks like one."""
    lowered = text.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def load_config(path="config.json", overrides=None):
    """
    Build the settings: DEFAULTS, then config.json (if it exists), then any --set overrides.
    overrides: list of strings like "classifier.straight_min_elbow_deg=140".
    """
    config = copy.deepcopy(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as f:
            _merge(config, json.load(f))
    for item in overrides or []:
        if "=" not in item or "." not in item.split("=", 1)[0]:
            raise ValueError(f"--set needs the form section.name=value, got: {item}")
        name, value = item.split("=", 1)
        section, key = name.split(".", 1)
        config.setdefault(section, {})[key] = _parse_value(value)
    return config