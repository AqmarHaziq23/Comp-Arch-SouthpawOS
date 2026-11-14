"""
vision_config.py - Southpaw OS, computer vision (Member D)

One place for every tunable setting of the vision module.

* DEFAULTS below holds a sensible value for every setting, so the code always runs,
  even if config.json is missing or older than the code.
* config.json (same sections and names) overrides any of these values. Tune there, not in code.
* Scripts also accept   --set section.name=value   to try a value once without editing files,
  e.g.  python vision_main.py --set guard.rear_max_below_mouth=0.2

Distances are in "body lengths" (the torso, shoulders to hips, as seen by the camera:
about 0.5 m on an adult), so the same numbers work whether the player stands near or far.
"""

import copy
import json
import os

DEFAULTS = {
    "camera": {
        "source": "0",            # webcam number (0 = /dev/video0 on the Pi; the C922 is often 1 on a laptop),
                                  # a /dev/v4l/by-id/... path, or a video file
        "width": 640,
        "height": 480,
        "fps": 30,
        "process_fps": 9,         # run the pose model at most this often (0 = every frame). The Pi 4 manages
                                  # ~9 fps anyway; on a fast PC this keeps results the same as on the Pi.
                                  # Guard posture changes slowly, so 9 checks a second is plenty.
    },
    "pose": {
        "model_path": "pose_landmarker_lite.task",
        "min_detection_confidence": 0.5,
        "min_presence_confidence": 0.5,
        "min_tracking_confidence": 0.5,
        "min_visibility": 0.5,    # ignore body points MediaPipe thinks are hidden
    },
    "player": {
        "stance": "orthodox",     # orthodox: left hand leads. southpaw: right hand leads.
    },
    "guard": {
        # Tuned on Johanan's first session (14 Nov): in guard, fists sat 0-0.15 below the mouth;
        # lowered hands (collarbone) 0.3-0.4; dropped hands 0.4+. 0.25 = the bottom of the chin.
        "rear_max_below_mouth": 0.25,   # rear fist may sit at most this far BELOW the mouth
        "lead_max_below_mouth": 0.25,   # same for the lead fist (make it smaller to demand a higher lead hand)
        "max_side_reach": 0.8,          # fist at most this far sideways from the nose (guard: 0.16 typical, 0.43 at most)
        "drop_hold_s": 0.4,             # a hand must stay down this long to count as a drop
        "scale_smoothing": 0.1,         # how fast the body-size estimate adapts (0-1, small = steady)
        "shoulder_to_body_length": 1.9, # only if the hips were never seen: body length ~ shoulder width x this
    },
    "output": {
        "hub_host": "127.0.0.1",  # where the hub listens (Member C). Use the Pi's IP if vision runs on a laptop.
        "hub_port": 5005,
        "heartbeat_interval_s": 1.0,   # "vision is alive" message this often
        "enabled": True,
    },
    "debug": {
        "show_window": True,      # needs a desktop (or Raspberry Pi Connect); turned off by --no-display
        "record_path": None,      # save the overlay video here (None = don't)
        "print_changes": True,    # print a line whenever a hand drops / comes back up
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
    overrides: list of strings like "guard.drop_hold_s=0.5".
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


if __name__ == "__main__":
    # python vision_config.py  ->  print the full settings (defaults + config.json) as JSON
    print(json.dumps(load_config(), indent=2))
