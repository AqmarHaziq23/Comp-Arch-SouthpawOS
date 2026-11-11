"""
camera.py - Southpaw OS, computer vision (Member D)

Video sources for vision_main.py. Both give frames the same way:
    got = source.read(last_id, timeout=1.0)   ->  (frame_id, frame, capture_time)  or None

* LiveCamera  - the webcam. A background thread keeps ONLY the newest frame (so a slow
                pose model never works on old frames) and re-opens the camera if it is
                unplugged and plugged back in.
* ClipCamera  - a recorded video, for testing and for the demo-day backup. In "realtime"
                mode it behaves like a live camera (frames are skipped while the Pi is busy,
                timestamps use the live clock), so the hub can't tell the difference.

capture_time is time.monotonic() seconds. On Linux that clock is shared by every process
on the machine, so the hub (Member C) can compare it with its own time.monotonic().
"""

import os
import threading
import time

import cv2


def open_source(source, width=640, height=480, fps=30, realtime=True):
    """Pick the right source: a number or /dev/... path = webcam, anything else = video file."""
    source = str(source)
    if source.isdigit() or source.startswith("/dev/"):
        return LiveCamera(source, width, height, fps)
    return ClipCamera(source, realtime=realtime)


class LiveCamera:
    def __init__(self, source, width, height, fps):
        self.source = int(source) if str(source).isdigit() else source
        self.width, self.height, self.fps = width, height, fps
        self.lock = threading.Lock()
        self.frame, self.frame_time, self.frame_id = None, 0.0, 0
        self.running = True
        self.connected = False
        self.cap = None
        self._open()
        if not self.connected:
            raise RuntimeError(f"Could not open webcam {source}. Is it plugged in? (try v4l2-ctl --list-devices)")
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _open(self):
        """(Re)open the webcam with our settings. Returns True if it worked."""
        if self.cap is not None:
            self.cap.release()
        cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(self.source)        # fallback for Windows/macOS testing
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # compressed = full fps over USB
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap = cap
        self.connected = cap.isOpened()
        return self.connected

    def _reader(self):
        """Background thread: keep overwriting the newest frame; reconnect if frames stop."""
        last_good = time.monotonic()
        while self.running:
            ok, frame = self.cap.read() if self.connected else (False, None)
            now = time.monotonic()
            if ok:
                last_good = now
                with self.lock:
                    self.frame, self.frame_time = frame, now
                    self.frame_id += 1
                continue
            # No frame. After 2 s without frames, assume the camera was unplugged: retry every second.
            if now - last_good > 2.0:
                self.connected = False
                if self._open():
                    print("Webcam reconnected")
                    last_good = time.monotonic()
                else:
                    time.sleep(1.0)
            else:
                time.sleep(0.01)

    def read(self, last_id, timeout=1.0):
        """Newest frame newer than last_id, waiting up to timeout seconds. None if none arrived."""
        deadline = time.monotonic() + timeout
        while self.running and time.monotonic() < deadline:
            with self.lock:
                if self.frame is not None and self.frame_id != last_id:
                    return self.frame_id, self.frame.copy(), self.frame_time
            time.sleep(0.002)
        return None

    @property
    def ended(self):
        return False                      # a webcam never "ends"

    def size(self):
        return (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width,
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height)

    def describe(self):
        w, h = self.size()
        fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        text = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
        return f"webcam {self.source}: {w}x{h} @ {self.cap.get(cv2.CAP_PROP_FPS):.0f} fps, {text}"

    def close(self):
        self.running = False
        self.thread.join(timeout=2)
        if self.cap is not None:
            self.cap.release()


class ClipCamera:
    """
    A recorded clip pretending to be a camera.
    realtime=True:  plays at real speed; frames the Pi is too slow for are skipped (like live);
                    capture times are live time.monotonic() values.
    realtime=False: every frame, as fast as possible; capture times come from the clip
                    (frames.csv from record_clips.py if present, else frame number / fps).
    """

    def __init__(self, path, realtime=True):
        if not os.path.exists(path):
            raise RuntimeError(f"Video file not found: {path}")
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open video file {path}")
        self.path = path
        self.realtime = realtime
        self.clip_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.frame_times = self._load_frame_times(path)
        self.next_index = 0               # next frame number in the file
        self.start = None                 # live time when playback started
        self._ended = False

    @staticmethod
    def _load_frame_times(path):
        """Use the real capture times recorded by record_clips.py, if they are next to the video."""
        frames_csv = os.path.join(os.path.dirname(path), "frames.csv")
        if not os.path.exists(frames_csv):
            return None
        import csv
        with open(frames_csv) as f:
            return [float(r["time_s"]) for r in csv.DictReader(f)]

    def _clip_time(self, index):
        if self.frame_times and index < len(self.frame_times):
            return self.frame_times[index]
        return index / self.clip_fps

    def read(self, last_id, timeout=1.0):
        if self._ended:
            return None
        if not self.realtime:
            ok, frame = self.cap.read()
            if not ok:
                self._ended = True
                return None
            index = self.next_index
            self.next_index += 1
            return index + 1, frame, self._clip_time(index)

        # Realtime: jump to the newest frame that "exists" at this moment, like a live camera
        now = time.monotonic()
        if self.start is None:
            self.start = now - self._clip_time(0)
        elapsed = now - self.start
        target = self.next_index
        while target + 1 < (len(self.frame_times) if self.frame_times else 10 ** 9) \
                and self._clip_time(target + 1) <= elapsed:
            target += 1
        # Skip the frames we were too slow for (grab() is cheap: it doesn't decode)
        while self.next_index < target:
            if not self.cap.grab():
                self._ended = True
                return None
            self.next_index += 1
        # Not yet time for this frame? Wait for it, like waiting for the camera.
        wait = self._clip_time(self.next_index) - elapsed
        if wait > 0:
            time.sleep(min(wait, timeout))
        ok, frame = self.cap.read()
        if not ok:
            self._ended = True
            return None
        index = self.next_index
        self.next_index += 1
        return index + 1, frame, self.start + self._clip_time(index)

    @property
    def ended(self):
        return self._ended

    def size(self):
        return int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def describe(self):
        w, h = self.size()
        mode = "real-time playback" if self.realtime else "every frame, as fast as possible"
        return f"clip {self.path}: {w}x{h}, {self.clip_fps:.0f} fps ({mode})"

    def close(self):
        self.cap.release()
