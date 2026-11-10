"""
record_clips.py - Southpaw OS, computer vision (Member D)

Records LABELLED punch clips for tuning and testing the punch detector.

  * Records the raw webcam video at full speed (no pose model running, so no frames are lost).
  * Shows a big on-screen cue for each punch to throw ("LEFT HOOK  3/10"), one every few seconds.
  * Logs exactly which frame each cue appeared on, so every punch in the video has a label.

Recording and display run separately: a background thread saves EVERY camera frame and logs
the cues, while the cue window just shows the newest frame as often as it can. So a slow
display (e.g. through Raspberry Pi Connect) can never make the recording drop frames.

Later, evaluate.py replays these clips through the detector and compares what it detected
against these labels (a confusion matrix: e.g. "uppercut called hook 3 times out of 10").

Output, in recordings/<session>/  (recordings/ is git-ignored - upload clips to the team Drive):
  video.avi    raw webcam video (MJPG), no overlay
  labels.csv   one row per cued punch: which punch, which frame/time the cue appeared
  frames.csv   the capture time of every frame (lets us line labels up exactly with the video)
  meta.json    session details: camera angle, stance, resolution, date, notes

Examples (run inside the venv, on the Pi desktop so you can see the cues):
  python record_clips.py --session 45R_run1 --angle 45-right
  python record_clips.py --session 45R_run2 --angle 45-right --reps 5 --interval 3
  python record_clips.py --session 45R_mixed1 --angle 45-right --shuffle   # random order instead of blocks

Stand where the cue screen is visible from your punching spot. Press Q in the window
(or Ctrl+C in the terminal) to stop early - everything recorded so far is still saved.
"""

import argparse
import csv
import datetime
import json
import os
import random
import threading
import time

import cv2

# Punches to record, as (hand, punch type, text shown on screen).
# Orthodox stance: left = lead hand (jab), right = rear hand (cross).
# For a southpaw athlete, the left straight is the cross and the right straight is the jab.
PUNCHES = [
    ("L", "jab", "LEFT JAB"),
    ("R", "cross", "RIGHT CROSS"),
    ("L", "hook", "LEFT HOOK"),
    ("R", "hook", "RIGHT HOOK"),
    ("L", "uppercut", "LEFT UPPERCUT"),
    ("R", "uppercut", "RIGHT UPPERCUT"),
]

# Colours (OpenCV uses BGR order)
GREEN = (0, 220, 0)
YELLOW = (0, 220, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (0, 0, 255)


def build_plan(reps, shuffle, countdown, interval, block_rest):
    """
    Make the list of punches to cue, each with the time (seconds after start) it should appear.
    Blocks (default): 10 left jabs, then 10 right crosses, ... - easiest to perform,
    with extra rest whenever the punch type changes.
    Shuffle: all punches mixed randomly - closer to a real combo game.
    """
    plan = []
    for hand, punch, text in PUNCHES:
        for rep in range(1, reps + 1):
            plan.append({"hand": hand, "punch": punch, "text": text, "rep": rep})
    if shuffle:
        random.shuffle(plan)

    t = float(countdown)
    for i, cue in enumerate(plan):
        new_block = i > 0 and (cue["hand"], cue["punch"]) != (plan[i - 1]["hand"], plan[i - 1]["punch"])
        if new_block and not shuffle:
            t += block_rest
        cue["planned_t"] = t
        cue["starts_block"] = (i == 0 or new_block) and not shuffle
        t += interval
    end_time = t + 1.5   # keep recording briefly after the last cue so the last punch is captured
    return plan, end_time


def draw_big_text(img, text, y, colour, scale=1.6, thickness=4):
    """Draw centred text with a black outline so it is readable on any background."""
    (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    x = (img.shape[1] - tw) // 2
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, BLACK, thickness + 6, cv2.LINE_AA)
    cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness, cv2.LINE_AA)


def open_camera(source, width, height, fps):
    """Open the webcam (number) or a video file (path, used for testing the script)."""
    if source.isdigit():
        cap = cv2.VideoCapture(int(source), cv2.CAP_V4L2)
        if not cap.isOpened():
            cap = cv2.VideoCapture(int(source))      # fallback for non-Linux machines
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # compressed = full 30 fps over USB
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
    else:
        cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source {source}")
    return cap


class Recorder(threading.Thread):
    """
    Background thread that does all the important work:
    read every frame -> save it -> log its time -> log a label whenever a cue is due.
    It never waits for the display, so the recording keeps the camera's full frame rate.
    The display only READS the shared state below (protected by a lock).
    """

    def __init__(self, cap, writer, plan, end_time, fps, is_file, labels, frames, print_cues):
        super().__init__(daemon=True)
        self.cap, self.writer = cap, writer
        self.plan, self.end_time = plan, end_time
        self.fps, self.is_file = fps, is_file
        self.labels, self.frames = labels, frames
        self.print_cues = print_cues

        self.lock = threading.Lock()
        self.stop_requested = False
        # Shared state the display reads:
        self.latest_frame = None   # newest camera frame
        self.now = 0.0             # seconds since recording started
        self.next_cue = 0          # index of the next cue in the plan
        self.cue_shown_at = -99.0  # when the most recent cue appeared
        self.frame_index = 0       # frames saved so far
        self.ended_reason = ""

    def run(self):
        start = time.monotonic()
        while not self.stop_requested:
            ok, frame = self.cap.read()
            if not ok:
                self.ended_reason = "video source ended"
                break

            # Time since recording started. For a test video file, use the clip's own clock.
            now = (self.frame_index / self.fps) if self.is_file else (time.monotonic() - start)

            # 1. Save the RAW frame (cue text is only drawn on the display copy)
            self.writer.write(frame)
            self.frames.writerow([self.frame_index, f"{now:.4f}"])

            # 2. Is the next cue due? Log which frame it appeared on.
            new_cue = None
            if self.next_cue < len(self.plan) and now >= self.plan[self.next_cue]["planned_t"]:
                cue = self.plan[self.next_cue]
                self.labels.writerow([self.next_cue, cue["hand"], cue["punch"], cue["rep"],
                                      self.frame_index, f"{now:.4f}"])
                new_cue = cue

            # 3. Publish the new state for the display
            with self.lock:
                self.latest_frame = frame
                self.now = now
                if new_cue is not None:
                    self.next_cue += 1
                    self.cue_shown_at = now
                self.frame_index += 1

            if new_cue is not None and self.print_cues:
                print(f"[{now:6.2f}s] cue {self.next_cue}/{len(self.plan)}: "
                      f"{new_cue['text']}  {new_cue['rep']}")

            if now >= self.end_time:
                self.ended_reason = "finished"
                break
        if self.stop_requested:
            self.ended_reason = "stopped early"

    def snapshot(self):
        """Copy of the shared state, for the display."""
        with self.lock:
            return (self.latest_frame, self.now, self.next_cue, self.cue_shown_at, self.frame_index)


def render_cue_screen(frame, now, next_cue, cue_shown_at, plan, args):
    """Build the cue screen: the camera view with big instructions on top."""
    display = frame.copy()
    h = display.shape[0]
    last = plan[next_cue - 1] if next_cue > 0 else None
    upcoming = plan[next_cue] if next_cue < len(plan) else None

    if now < args.countdown:
        draw_big_text(display, f"GET READY  {int(args.countdown - now) + 1}", h // 2, YELLOW)
    elif upcoming and upcoming["starts_block"] and next_cue > 0 and now - cue_shown_at > args.interval * 0.8:
        # Resting between punch types: say what's coming next
        draw_big_text(display, "NEXT: " + upcoming["text"], h // 2, YELLOW, scale=1.2)
    elif last:
        # Flash green for the first 0.4 s of each cue = "throw now", then white
        colour = GREEN if now - cue_shown_at < 0.4 else WHITE
        draw_big_text(display, f"{last['text']}  {last['rep']}/{args.reps}", h // 2, colour)
    draw_big_text(display, f"REC  {now:5.1f}s   {next_cue}/{len(plan)}", 40, RED, scale=0.7, thickness=2)
    return display


def main():
    parser = argparse.ArgumentParser(description="Record labelled punch clips")
    parser.add_argument("--session", required=True, help="name for this recording, e.g. 45R_run1")
    parser.add_argument("--angle", default="unknown", help="camera angle, e.g. 45-right (saved in meta.json)")
    parser.add_argument("--stance", default="orthodox", choices=["orthodox", "southpaw"])
    parser.add_argument("--athlete", default="", help="who is punching (optional)")
    parser.add_argument("--notes", default="", help="anything else worth remembering (lighting, distance...)")
    parser.add_argument("--reps", type=int, default=10, help="how many of each punch")
    parser.add_argument("--interval", type=float, default=2.5, help="seconds between cues")
    parser.add_argument("--countdown", type=int, default=5, help="seconds before the first cue")
    parser.add_argument("--block-rest", type=float, default=4.0, help="extra seconds between punch types")
    parser.add_argument("--shuffle", action="store_true", help="random punch order instead of blocks")
    parser.add_argument("--source", default="0", help="webcam number, or a video file (for testing)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--out", default="recordings", help="folder to save sessions in")
    parser.add_argument("--no-display", action="store_true", help="no window; cues are printed instead")
    args = parser.parse_args()

    # --- Prepare the output folder ------------------------------------------------------
    session_dir = os.path.join(args.out, args.session)
    if os.path.exists(session_dir):
        raise SystemExit(f"{session_dir} already exists - pick a new --session name so nothing is overwritten")
    os.makedirs(session_dir)

    cap = open_camera(args.source, args.width, args.height, args.fps)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cam_fps = cap.get(cv2.CAP_PROP_FPS) or args.fps
    is_file = not args.source.isdigit()
    print(f"Recording {width}x{height} @ {cam_fps:.0f} fps into {session_dir}/")

    # MJPG in an .avi: cheap for the Pi to write, so recording keeps up with the camera
    writer = cv2.VideoWriter(os.path.join(session_dir, "video.avi"),
                             cv2.VideoWriter_fourcc(*"MJPG"), cam_fps, (width, height))

    plan, end_time = build_plan(args.reps, args.shuffle, args.countdown, args.interval, args.block_rest)
    print(f"{len(plan)} punches planned, about {end_time:.0f} s in total")

    labels_file = open(os.path.join(session_dir, "labels.csv"), "w", newline="")
    labels = csv.writer(labels_file)
    labels.writerow(["cue_index", "hand", "punch", "rep", "cue_frame", "cue_time_s"])
    frames_file = open(os.path.join(session_dir, "frames.csv"), "w", newline="")
    frames = csv.writer(frames_file)
    frames.writerow(["frame", "time_s"])

    recorder = Recorder(cap, writer, plan, end_time, cam_fps, is_file, labels, frames,
                        print_cues=args.no_display)
    recorder.start()

    try:
        if args.no_display:
            # Nothing to draw: just wait for the recording to finish
            while recorder.is_alive():
                recorder.join(timeout=0.2)
        else:
            # Display loop: show the newest frame with the cue text, as often as the screen allows.
            # If this is slow, the display simply skips frames - the recorder still saves all of them.
            window = "Southpaw OS - clip recorder (Q to stop)"
            while recorder.is_alive():
                frame, now, next_cue, cue_shown_at, _ = recorder.snapshot()
                if frame is not None:
                    cv2.imshow(window, render_cue_screen(frame, now, next_cue, cue_shown_at, plan, args))
                if cv2.waitKey(30) & 0xFF == ord("q"):     # ~30 ms pause = at most ~30 screen updates/s
                    recorder.stop_requested = True
    except KeyboardInterrupt:
        recorder.stop_requested = True
    finally:
        recorder.join(timeout=3)
        _, duration, cues_shown, _, frame_count = recorder.snapshot()
        cap.release()
        writer.release()
        labels_file.close()
        frames_file.close()
        cv2.destroyAllWindows()

        measured_fps = round(frame_count / duration, 2) if duration > 0 else 0
        # Session details, so we know later how each clip was recorded
        meta = {
            "session": args.session,
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "angle": args.angle,
            "stance": args.stance,
            "athlete": args.athlete,
            "notes": args.notes,
            "width": width, "height": height,
            "camera_fps_requested": cam_fps,
            "frames_recorded": frame_count,
            "duration_s": round(duration, 2),
            "measured_fps": measured_fps,
            "cues_shown": cues_shown,
            "cues_planned": len(plan),
            "reps": args.reps, "interval_s": args.interval, "shuffle": args.shuffle,
            "ended": recorder.ended_reason,
        }
        with open(os.path.join(session_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

        print(f"\nSaved {frame_count} frames ({duration:.1f} s, {measured_fps} fps) "
              f"and {cues_shown}/{len(plan)} labelled punches to {session_dir}/ ({recorder.ended_reason})")
        if measured_fps and measured_fps < cam_fps * 0.9:
            print("WARNING: recorded noticeably slower than the camera - frames were dropped. "
                  "Close other programs and check the lighting (dim light lowers webcam fps).")


if __name__ == "__main__":
    main()