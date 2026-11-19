"""
snapshot.py - Southpaw OS, computer vision (Member D)

The "camera check" for the Telegram bot's /camera command.

About once a second, vision saves two small files into one folder:
    latest.jpg    the camera picture WITH the overlay (skeleton, guard lines, fists circled)
    latest.json   what vision can see right now, for example
                  {"t": 1763542000.5, "camera": true, "person": true, "face": true, "hips": true,
                   "fps": 8.9, "lead": "L", "image": true,
                   "hands": {"L": {"seen": true, "up": true}, "R": {"seen": false, "up": null}}}
The bot (telegram_bot/camera_check.py) reads them when a player sends /camera, so the player
can see on their phone whether the camera is set up properly and they are in frame.

* Each file is written to a temporary name first and then renamed, so the bot never reads
  half a file.
* Each new picture replaces the last one: there is only ever ONE picture, a second old at most.
* The folder: on Linux (the Pi) /dev/shm/southpaw-vision. /dev/shm is kept in memory, not on
  the SD card, so saving a picture every second doesn't wear the card out, and the picture is
  gone after a reboot. Elsewhere (Windows): the system's temp folder. Only this account can
  open the folder (the pictures show the room).

No OpenCV/MediaPipe in here (vision_main.py makes the JPEG), so test_guard.py can test it anywhere.
"""

import json
import os
import tempfile
import time

FOLDER_NAME = "southpaw-vision"


def default_folder():
    """Where the files go if config.json doesn't say (the bot uses the same rule)."""
    base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    return os.path.join(base, FOLDER_NAME)


def _seen(pose, name, min_vis):
    return pose is not None and name in pose and pose[name].vis >= min_vis


def camera_report(pose, hands, camera_ok, fps, lead, min_vis, now=None):
    """What the camera can see in this frame, as a small dict (it becomes latest.json).

    pose:  {name: Point} from pose.py, or None if nobody is in view
    hands: {"L": HandGuard, "R": HandGuard} from GuardChecker.check
    """
    person = pose is not None
    return {
        "t": time.time() if now is None else now,      # wall-clock time, so the bot can tell how old it is
        "camera": bool(camera_ok),
        "person": person,
        # The guard check needs the face (the "hands up" line is measured from the mouth)...
        "face": _seen(pose, "NOSE", min_vis) or (_seen(pose, "MOUTH_L", min_vis) and _seen(pose, "MOUTH_R", min_vis)),
        # ... and the hips (body size: shoulders to hips). Without them it only guesses.
        "hips": _seen(pose, "L_HIP", min_vis) and _seen(pose, "R_HIP", min_vis),
        "fps": round(float(fps), 1),
        "lead": lead,
        "hands": {side: {"seen": _seen(pose, f"{side}_WRIST", min_vis),
                         "up": hands[side].up if side in hands else None}
                  for side in "LR"},
    }


class SnapshotWriter:
    """Saves latest.jpg + latest.json at most once every interval_s seconds."""

    def __init__(self, folder=None, interval_s=1.0, clock=time.monotonic):
        self.folder = folder or default_folder()
        self.interval_s = interval_s
        self.clock = clock
        self._last = None                   # when we last saved (clock time)
        self.errors = 0
        self._make_folder()

    def _make_folder(self):
        try:
            os.makedirs(self.folder, exist_ok=True)
            os.chmod(self.folder, 0o700)    # only this account may look at the pictures (ignored on Windows)
        except OSError:
            pass                            # can't make it: write() then just counts errors, vision carries on

    def due(self):
        """True if it's time for the next snapshot (so vision only makes a JPEG when needed)."""
        return self._last is None or self.clock() - self._last >= self.interval_s

    def write(self, jpeg_bytes, report):
        """Save the picture (or None: no picture, e.g. the camera is gone) and the report.
        Never raises: a full disk or a missing folder must not stop the guard tracking."""
        self._last = self.clock()
        report = dict(report, image=jpeg_bytes is not None)
        for attempt in (1, 2):
            try:
                if jpeg_bytes is not None:
                    self._replace("latest.jpg", jpeg_bytes)     # picture first, then the report about it
                self._replace("latest.json", json.dumps(report).encode("utf-8"))
                return True
            except FileNotFoundError:
                # The folder was deleted. On the Pi this happens when the account's last SSH login
                # ends: Linux then cleans that account's files out of /dev/shm. Make it again, once.
                self._make_folder()
            except OSError:
                break
        self.errors += 1
        return False

    def _replace(self, name, data):
        """Write to name.tmp, then rename over name: the rename is all-or-nothing."""
        path = os.path.join(self.folder, name)
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
