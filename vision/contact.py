"""
contact.py - Southpaw OS, computer vision (Member D)

"Did the fist reach the bag?" - checks whether the fist (index-knuckle point) entered the
bag zone at any frame of a punch. The bag zone is an outline of the bag's hitting area
in the camera image, clicked once per camera setup with calibrate.py and saved in
calibration.local.json (git-ignored, because it depends on where the camera stands).

Returns True / False, or None when there is no calibration (so the hub knows vision
has no opinion, instead of thinking every punch missed).

Limitation: the camera image is flat. A fist can be IN FRONT of the bag in the image but
not touching it (short of the bag, towards the camera). The bag board's impact sensor is
the main judge of "landed"; this is the second opinion. A side-on camera reduces the
problem because then "short of the bag" shows up as a gap in the image.
"""

import json
import os

import cv2
import numpy as np


class ContactChecker:
    def __init__(self, config, frame_size):
        self.cfg = config["contact"]
        self.polygon = None
        path = self.cfg["calibration_file"]
        if path and os.path.exists(path):
            with open(path) as f:
                calib = json.load(f)
            points = np.array(calib["bag_zone"], dtype=np.float32)
            # If the calibration was made at another resolution, scale the corners to match
            cw, ch = calib.get("width", frame_size[0]), calib.get("height", frame_size[1])
            points[:, 0] *= frame_size[0] / cw
            points[:, 1] *= frame_size[1] / ch
            if len(points) >= 3:
                self.polygon = points.reshape(-1, 1, 2)

    @property
    def calibrated(self):
        return self.polygon is not None

    def check(self, event):
        """True if the fist entered the bag zone during the punch, False if not, None if uncalibrated."""
        if self.polygon is None:
            return None
        margin = self.cfg["margin_px"]
        for x, y in event.knuckle_path:
            # pointPolygonTest with measureDist=True: + inside, - outside (pixels from the edge)
            if cv2.pointPolygonTest(self.polygon, (float(x), float(y)), True) >= -margin:
                return True
        return False

    def draw(self, frame, colour=(0, 200, 255)):
        """Draw the bag zone outline on the overlay."""
        if self.polygon is not None:
            cv2.polylines(frame, [self.polygon.astype(np.int32)], True, colour, 2)
            x, y = self.polygon[0][0]
            cv2.putText(frame, "BAG", (int(x) + 4, int(y) + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2)
        return frame
