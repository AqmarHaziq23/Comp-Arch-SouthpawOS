"""
camera_check.py - Southpaw OS Telegram bot

The /camera command: "is the camera set up properly, and am I in frame?"

Vision (vision/snapshot.py) saves, about once a second, into one folder on the Pi:
    latest.jpg    the camera picture with the overlay (skeleton, guard lines, fists circled)
    latest.json   what it can see: person? face? hips? each hand seen / up? checks per second
This file reads them and turns them into a short, friendly check for the player:

    📷 Camera check (8.9 checks/s)
    ✅ I can see you
    ⚠️ Left hand (lead): can't see it ...

The folder is found the same way vision finds it (/dev/shm/southpaw-vision on the Pi), or set
VISION_SNAPSHOT_DIR in .env if vision uses another folder. Both programs must run on the same Pi
under the same account.

No Telegram code in here, so test_core.py can test it.
"""

import json
import os
import tempfile

FOLDER_NAME = "southpaw-vision"      # the same name as in vision/snapshot.py
MAX_AGE_S = 5.0                      # an older picture means vision has stopped
SLOW_FPS = 5.0                       # fewer checks a second than this: warn that the Pi is busy
HAND = {"L": "Left", "R": "Right"}


def snapshot_folder():
    """Where vision saves its picture (same rule as vision/snapshot.py default_folder)."""
    if os.environ.get("VISION_SNAPSHOT_DIR"):
        return os.environ["VISION_SNAPSHOT_DIR"]
    base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    return os.path.join(base, FOLDER_NAME)


def read_snapshot(folder=None):
    """(report, jpeg_bytes) from vision's latest files. (None, None) if vision never saved one;
    jpeg_bytes is None if there's no picture (for example the camera is unplugged)."""
    folder = folder or snapshot_folder()
    try:
        with open(os.path.join(folder, "latest.json"), encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return None, None
    if not isinstance(report, dict):
        return None, None
    photo = None
    if report.get("image"):
        try:
            with open(os.path.join(folder, "latest.jpg"), "rb") as f:
                photo = f.read()
        except OSError:
            pass
    return report, photo


def age_s(report, now):
    """How many seconds old the report is."""
    try:
        return max(0.0, now - float(report.get("t", 0)))
    except (TypeError, ValueError):
        return float("inf")


def is_fresh(report, now, max_age_s=MAX_AGE_S):
    return report is not None and age_s(report, now) <= max_age_s


def _hand(report, side):
    hand = (report.get("hands") or {}).get(side) or {}
    role = "lead" if report.get("lead", "L") == side else "rear"
    return hand, f"{HAND[side]} hand ({role})"


def problems(report):
    """Short names of what the camera can't see, e.g. ["your face", "your left hand"]."""
    found = []
    if not report.get("face"):
        found.append("your face")
    for side in "LR":
        hand, _ = _hand(report, side)
        if not hand.get("seen"):
            found.append(f"your {HAND[side].lower()} hand")
    if not report.get("hips"):
        found.append("your hips")
    return found


def full_text(report, now, max_age_s=MAX_AGE_S):
    """The /camera reply (Telegram HTML), to go with the picture."""
    title = "📷 <b>Camera check</b>"
    if report is None:
        return (f"{title}\n⚠️ Vision isn't running, so there's no camera picture.\n"
                "The game still works, just without the guard stats.")
    if not is_fresh(report, now, max_age_s):
        return (f"{title}\n⚠️ Vision stopped about {age_s(report, now):.0f} s ago, so there's no fresh picture.\n"
                "Is the vision program running on the Pi?")
    if not report.get("camera"):
        return f"{title}\n⚠️ The camera isn't sending pictures. Is it plugged into the Pi?"

    fps = float(report.get("fps") or 0)
    lines = [f"{title} ({fps:.1f} checks/s)"]
    if not report.get("person"):
        lines.append("⚠️ I can't see anyone. Stand where you box, facing the camera, then send /camera again.")
    else:
        lines.append("✅ I can see you")
        lines.append("✅ Face" if report.get("face") else
                     "⚠️ I can't see your face: is the camera too low, or are you too close?")
        for side in "LR":
            hand, name = _hand(report, side)
            if not hand.get("seen"):
                lines.append(f"⚠️ {name}: can't see it. Turn the camera or step back so both hands are in the picture")
            elif hand.get("up"):
                lines.append(f"✅ {name}: up")
            else:
                lines.append(f"✅ {name}: seen (down right now)")
        if not report.get("hips"):
            lines.append("⚠️ I can't see your hips: step back or tilt the camera down. I need you from head to hips")
        if not problems(report):
            lines.append("\n👍 All set: the camera can see everything it needs.")
    if 0 < fps < SLOW_FPS:
        lines.append(f"⚠️ Only {fps:.1f} checks a second: the Pi is busy, so the guard stats may be rough.")
    lines.append(f"\n<i>Picture from {age_s(report, now):.0f} s ago. "
                 "Circles: green = hand up, red = down, grey = can't tell.</i>")
    return "\n".join(lines)


def short_line(report, now, max_age_s=MAX_AGE_S):
    """One line for the "card scanned" message, or None if vision isn't running
    (vision is optional, so then the bot says nothing about the camera)."""
    if not is_fresh(report, now, max_age_s):
        return None
    if not report.get("camera"):
        return "📷 Camera: ⚠️ not sending pictures. Send /camera to check."
    if not report.get("person"):
        return "📷 Camera: can't see you yet. Stand in your boxing spot; send /camera to check."
    missing = problems(report)
    if missing:
        listed = missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " or " + missing[-1]
        return f"📷 Camera: ⚠️ can't see {listed}. Send /camera to check."
    return "📷 Camera: ✅ sees you and both hands."
