"""
pose_preview.py - Southpaw OS, computer vision (Member D)

First test script for the vision module:
  * reads frames from the webcam (or a recorded video file),
  * runs MediaPipe PoseLandmarker (lite model) on each frame,
  * draws the upper-body skeleton and a trail behind each wrist,
  * shows how fast it runs (FPS and milliseconds per frame).

Use it to answer the first big question: how many frames per second can
the Raspberry Pi 4 manage? (10+ fps = stay on the Pi, under ~6 = move vision to a laptop.)

Examples (run inside the venv):
  python pose_preview.py                          # webcam, show a window (run this on the Pi desktop)
  python pose_preview.py --no-display             # webcam, no window, just print FPS (works over SSH)
  python pose_preview.py --record out.mp4         # also save the overlay video
  python pose_preview.py --source clip.mp4        # use a recorded video instead of the webcam

Press Q in the window (or Ctrl+C in the terminal) to stop.

Uses MediaPipe's Tasks API (PoseLandmarker). The older "mp.solutions.pose" API in
most online tutorials no longer exists in current MediaPipe versions.
Model: pose_landmarker_lite.task from Google's MediaPipe model page.
"""

import argparse
import collections
import threading
import sys
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

# Fastest webcam driver for this OS: DirectShow on Windows (the default, Media Foundation,
# can take 10-30 s to open a USB webcam), V4L2 on Linux / the Pi
WEBCAM_BACKEND = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_V4L2


# ---------------------------------------------------------------------------
# Body points (landmarks) we care about. MediaPipe numbers 33 points on the body.
# "Left"/"right" are the PERSON's left/right, not the left/right of the image.
# ---------------------------------------------------------------------------
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_INDEX, R_INDEX = 19, 20      # index-finger knuckle area, roughly where the fist is
L_HIP, R_HIP = 23, 24

# Lines to draw between points to make the upper-body skeleton
SKELETON = [
    (L_SHOULDER, R_SHOULDER),
    (L_SHOULDER, L_ELBOW), (L_ELBOW, L_WRIST), (L_WRIST, L_INDEX),
    (R_SHOULDER, R_ELBOW), (R_ELBOW, R_WRIST), (R_WRIST, R_INDEX),
    (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP), (L_HIP, R_HIP),
]

# Colours are in OpenCV's BGR order (blue, green, red), not RGB
COLOUR_LEFT = (255, 160, 0)     # blue-ish for the left arm
COLOUR_RIGHT = (0, 140, 255)    # orange for the right arm
COLOUR_BODY = (200, 200, 200)   # grey for shoulders/hips
COLOUR_TEXT = (255, 255, 255)

MIN_VISIBILITY = 0.5   # ignore points MediaPipe thinks are hidden or off-screen
TRAIL_LENGTH = 15      # how many past wrist positions to draw behind each wrist


class LatestFrameCamera:
    """
    Reads the webcam in a background thread and keeps ONLY the newest frame.

    Why: if processing is slower than the camera (very likely on a Pi), frames
    pile up in a queue and we'd be analysing the past. Here, old frames are
    simply thrown away, so we always work on what is happening now.
    """

    def __init__(self, index, width, height, fps):
        # Use the fastest webcam driver for this OS (see WEBCAM_BACKEND)
        self.cap = cv2.VideoCapture(index, WEBCAM_BACKEND)
        if not self.cap.isOpened():
            # Fall back to OpenCV's default backend (e.g. when testing on Windows)
            self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open webcam {index}. Is it plugged in? Try --source 1")

        # Ask the camera for compressed MJPG frames: much faster over USB than raw YUYV
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # keep the driver's own queue as short as possible

        self.lock = threading.Lock()
        self.frame = None          # newest frame
        self.frame_time = 0.0      # when that frame was captured (time.monotonic() seconds)
        self.frame_id = 0          # counts up by 1 for every new frame
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        # Runs forever in the background, overwriting self.frame with each new frame
        while self.running:
            ok, frame = self.cap.read()
            now = time.monotonic()
            if not ok:
                time.sleep(0.01)
                continue
            with self.lock:
                self.frame, self.frame_time = frame, now
                self.frame_id += 1

    def read(self, last_id):
        """Wait until there is a frame newer than last_id, then return (id, frame, capture_time)."""
        while self.running:
            with self.lock:
                if self.frame_id != last_id and self.frame is not None:
                    return self.frame_id, self.frame.copy(), self.frame_time
            time.sleep(0.002)
        return None

    def actual_settings(self):
        """What the camera actually agreed to (it may ignore some requests)."""
        fourcc = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_text = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4))
        return (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                self.cap.get(cv2.CAP_PROP_FPS), fourcc_text)

    def close(self):
        self.running = False
        self.thread.join(timeout=1)
        self.cap.release()


def to_pixels(landmark, width, height):
    """MediaPipe gives x/y as fractions of the image (0 to 1). Convert to pixel coordinates."""
    return int(landmark.x * width), int(landmark.y * height)


def draw_overlay(frame, landmarks, trails, stats):
    """Draw the skeleton, wrist trails and the speed numbers onto the frame."""
    h, w = frame.shape[:2]

    if landmarks is not None:
        # Skeleton lines (only between points that are actually visible)
        for a, b in SKELETON:
            pa, pb = landmarks[a], landmarks[b]
            if pa.visibility < MIN_VISIBILITY or pb.visibility < MIN_VISIBILITY:
                continue
            # Arm lines get the arm's colour; shoulder/hip (torso) lines are grey
            if b in (L_ELBOW, L_WRIST, L_INDEX):
                colour = COLOUR_LEFT
            elif b in (R_ELBOW, R_WRIST, R_INDEX):
                colour = COLOUR_RIGHT
            else:
                colour = COLOUR_BODY
            cv2.line(frame, to_pixels(pa, w, h), to_pixels(pb, w, h), colour, 3)

        # Dots on the joints we track
        for idx in (L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST):
            p = landmarks[idx]
            if p.visibility >= MIN_VISIBILITY:
                colour = COLOUR_LEFT if idx % 2 == 1 else COLOUR_RIGHT   # odd numbers = left side
                cv2.circle(frame, to_pixels(p, w, h), 6, colour, -1)

    # Wrist trails: older points are drawn thinner, so you can see the punch path
    for colour, trail in ((COLOUR_LEFT, trails["L"]), (COLOUR_RIGHT, trails["R"])):
        points = list(trail)
        for i in range(1, len(points)):
            thickness = max(1, int(6 * i / len(points)))
            cv2.line(frame, points[i - 1], points[i], colour, thickness)

    # Speed numbers in the top-left corner (with a dark box behind them so they're readable)
    lines = [
        f"Processing: {stats['fps']:.1f} fps",
        f"Pose model: {stats['infer_ms']:.0f} ms/frame",
        f"Frame age:  {stats['age_ms']:.0f} ms",
        "Person: yes" if landmarks is not None else "Person: NOT FOUND",
    ]
    cv2.rectangle(frame, (0, 0), (260, 22 + 22 * len(lines)), (0, 0, 0), -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (8, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOUR_TEXT, 1, cv2.LINE_AA)
    return frame


def main():
    parser = argparse.ArgumentParser(description="Southpaw OS pose preview + FPS test")
    parser.add_argument("--source", default="0",
                        help="webcam number (0 = /dev/video0) or a video file path")
    parser.add_argument("--model", default="pose_landmarker_lite.task", help="MediaPipe pose model file")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--cam-fps", type=int, default=30, help="frame rate to request from the webcam")
    parser.add_argument("--no-display", action="store_true", help="don't open a window (for SSH)")
    parser.add_argument("--record", help="save the overlay video to this .mp4 file")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after this many frames (0 = never)")
    args = parser.parse_args()

    # --- Load the pose model ---------------------------------------------------
    # VIDEO mode: we hand frames over one at a time with a timestamp. The same
    # code then works on the live webcam AND on recorded clips, which makes tuning easy.
    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=args.model),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,                          # only track one boxer
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)

    # --- Open the video source ---------------------------------------------------
    is_webcam = args.source.isdigit()
    camera, video = None, None
    if is_webcam:
        camera = LatestFrameCamera(int(args.source), args.width, args.height, args.cam_fps)
        w, h, cam_fps, fourcc = camera.actual_settings()
        print(f"Webcam opened: {w}x{h} @ {cam_fps:.0f} fps, format {fourcc}")
    else:
        video = cv2.VideoCapture(args.source)
        if not video.isOpened():
            raise RuntimeError(f"Could not open video file {args.source}")
        cam_fps = video.get(cv2.CAP_PROP_FPS) or 30
        print(f"Video file opened: {args.source} ({cam_fps:.0f} fps)")

    writer = None
    trails = {"L": collections.deque(maxlen=TRAIL_LENGTH), "R": collections.deque(maxlen=TRAIL_LENGTH)}
    stats = {"fps": 0.0, "infer_ms": 0.0, "age_ms": 0.0}
    last_id, frame_count, last_ts_ms = 0, 0, -1
    loop_start = time.monotonic()
    last_print = loop_start

    try:
        while True:
            # --- 1. Get the next frame ---------------------------------------------
            if is_webcam:
                got = camera.read(last_id)
                if got is None:
                    break
                last_id, frame, captured_at = got
                ts_ms = int(captured_at * 1000)
            else:
                ok, frame = video.read()
                if not ok:
                    break                              # end of the file
                captured_at = time.monotonic()
                ts_ms = int(video.get(cv2.CAP_PROP_POS_MSEC))   # time inside the clip
            # MediaPipe's VIDEO mode needs every timestamp to be larger than the last one
            ts_ms = max(ts_ms, last_ts_ms + 1)
            last_ts_ms = ts_ms

            # --- 2. Run the pose model --------------------------------------------------
            # OpenCV stores colours as BGR; MediaPipe expects RGB, so convert first
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            t0 = time.monotonic()
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)
            infer_ms = (time.monotonic() - t0) * 1000

            # --- 3. Update the wrist trails ---------------------------------------------
            landmarks = result.pose_landmarks[0] if result.pose_landmarks else None
            h, w = frame.shape[:2]
            for side, idx in (("L", L_WRIST), ("R", R_WRIST)):
                if landmarks is not None and landmarks[idx].visibility >= MIN_VISIBILITY:
                    trails[side].append(to_pixels(landmarks[idx], w, h))
                else:
                    trails[side].clear()    # wrist lost: start a fresh trail when it comes back

            # --- 4. Update the speed numbers (smoothed so they don't flicker) -------------
            frame_count += 1
            now = time.monotonic()
            elapsed = now - loop_start
            current_fps = frame_count / elapsed if elapsed > 0 else 0.0
            stats["fps"] = current_fps if frame_count < 10 else 0.9 * stats["fps"] + 0.1 * current_fps
            stats["infer_ms"] = infer_ms if frame_count == 1 else 0.9 * stats["infer_ms"] + 0.1 * infer_ms
            # "Frame age" = how old the frame was by the time we finished with it (latency)
            stats["age_ms"] = (now - captured_at) * 1000 if is_webcam else 0.0

            # --- 5. Draw, show, save ------------------------------------------------------
            draw_overlay(frame, landmarks, trails, stats)

            if args.record:
                if writer is None:   # create the video file once we know the frame size
                    out_fps = cam_fps if not is_webcam else max(5.0, round(stats["fps"]))
                    writer = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h))
                writer.write(frame)

            if not args.no_display:
                cv2.imshow("Southpaw OS - pose preview (press Q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            # In --no-display mode, print the numbers once a second instead
            if args.no_display and now - last_print >= 1.0:
                last_print = now
                person = "yes" if landmarks is not None else "no"
                print(f"{stats['fps']:5.1f} fps | pose model {stats['infer_ms']:4.0f} ms | "
                      f"frame age {stats['age_ms']:4.0f} ms | person: {person}")

            if args.max_frames and frame_count >= args.max_frames:
                break
    except KeyboardInterrupt:
        pass   # Ctrl+C: fall through to the clean-up below
    finally:
        total = time.monotonic() - loop_start
        if frame_count:
            print(f"\nProcessed {frame_count} frames in {total:.1f} s = {frame_count / total:.1f} fps on average")
        if camera:
            camera.close()
        if video:
            video.release()
        if writer:
            writer.release()
            print(f"Saved overlay video to {args.record}")
        landmarker.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()