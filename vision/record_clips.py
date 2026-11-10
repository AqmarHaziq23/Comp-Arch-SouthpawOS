"""
record_clips.py - Southpaw OS, computer vision (Member D)

Records LABELLED punch clips for tuning and testing the punch detector.

  * Records the raw webcam video at full speed (no pose model running, so no frames are lost).
  * Shows a big on-screen cue for each punch to throw ("LEFT HOOK  3/10"), one every few seconds.
  * Logs exactly which frame each cue appeared on, so every punch in the video has a label.

Later, evaluate.py replays these clips through the detector and compares what it detected
against these labels (a confusion matrix: e.g. "uppercut called hook 3 times out of 10").

Output, in recordings/<session>/  (recordings/ is git-ignored - upload clips to the team Drive):
  video.avi    raw webcam video (MJPG), no overlay
  labels.csv   one row per cued punch: which punch, which frame/time the cue appeared
  frames.csv   the capture time of every frame (lets us line labels up exactly with the video)
  meta.json    session details: camera angle, stance, resolution, date, notes

Examples (run inside the venv, on the Pi desktop so you can see the cues):
  python record_clips.py --session 45deg_run1 --angle 45
  python record_clips.py --session side_run1 --angle side --reps 5 --interval 3
  python record_clips.py --session mixed1 --shuffle          # random order instead of blocks

Stand where the cue screen is visible from your punching spot. Press Q to stop early
(everything recorded so far is still saved).
"""

import argparse
import csv
import datetime
import json
import os
import random
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


def build_plan(reps, shuffle):
    """
    Make the list of punches to cue, in order.
    Blocks (default): 10 left jabs, then 10 right crosses, ... - easiest to perform.
    Shuffle: all punches mixed randomly - closer to a real combo game.
    """
    plan = []
    for hand, punch, text in PUNCHES:
        for rep in range(1, reps + 1):
            plan.append({"hand": hand, "punch": punch, "text": text, "rep": rep})
    if shuffle:
        random.shuffle(plan)
    return plan


def draw_big_text(img, text, y, colour, scale=1.6, thickness=4):
    """Draw centred text with a black outline so it is readable on any background."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
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


def main():
    parser = argparse.ArgumentParser(description="Record labelled punch clips")
    parser.add_argument("--session", required=True, help="name for this recording, e.g. 45deg_run1")
    parser.add_argument("--angle", default="unknown", help="camera angle, e.g. 45 or side (saved in meta.json)")
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
    parser.add_argument("--no-display", action="store_true", help="no window; cues are printed (testing only)")
    args = parser.parse_args()

    # --- Prepare the output folder ---------------------------------------------------
    session_dir = os.path.join(args.out, args.session)
    if os.path.exists(session_dir):
        raise SystemExit(f"{session_dir} already exists - pick a new --session name so nothing is overwritten")
    os.makedirs(session_dir)

    cap = open_camera(args.source, args.width, args.height, args.fps)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cam_fps = cap.get(cv2.CAP_PROP_FPS) or args.fps
    print(f"Recording {width}x{height} @ {cam_fps:.0f} fps into {session_dir}/")

    # MJPG in an .avi: cheap for the Pi to write, so recording keeps up with the camera
    writer = cv2.VideoWriter(os.path.join(session_dir, "video.avi"),
                             cv2.VideoWriter_fourcc(*"MJPG"), cam_fps, (width, height))

    # --- Build the cue schedule ----------------------------------------------------------
    # Each cue gets a planned time (seconds after recording starts). Extra rest is added
    # whenever the punch type changes, so you can reset your stance.
    plan = build_plan(args.reps, args.shuffle)
    t = float(args.countdown)
    for i, cue in enumerate(plan):
        if i > 0 and (cue["hand"], cue["punch"]) != (plan[i - 1]["hand"], plan[i - 1]["punch"]) and not args.shuffle:
            t += args.block_rest
        cue["planned_t"] = t
        t += args.interval
    end_time = t + 1.5    # keep recording briefly after the last cue so the last punch is captured
    print(f"{len(plan)} punches planned, about {end_time:.0f} s in total")

    labels_file = open(os.path.join(session_dir, "labels.csv"), "w", newline="")
    labels = csv.writer(labels_file)
    labels.writerow(["cue_index", "hand", "punch", "rep", "cue_frame", "cue_time_s"])
    frames_file = open(os.path.join(session_dir, "frames.csv"), "w", newline="")
    frames = csv.writer(frames_file)
    frames.writerow(["frame", "time_s"])

    next_cue = 0          # index of the next cue in the plan
    current_text = ""     # cue text currently on screen
    cue_shown_at = -99.0  # when the current cue appeared (for the green flash)
    frame_index = 0
    is_file = not args.source.isdigit()
    start = time.monotonic()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Video source ended")
                break

            # Time since recording started. For a test video file, use the clip's own clock.
            now = (frame_index / cam_fps) if is_file else (time.monotonic() - start)

            # 1. Save the RAW frame first (the cue overlay is only drawn on the display copy)
            writer.write(frame)
            frames.writerow([frame_index, f"{now:.4f}"])

            # 2. Time for the next cue? Log which frame it appeared on.
            if next_cue < len(plan) and now >= plan[next_cue]["planned_t"]:
                cue = plan[next_cue]
                labels.writerow([next_cue, cue["hand"], cue["punch"], cue["rep"], frame_index, f"{now:.4f}"])
                current_text = f"{cue['text']}  {cue['rep']}/{args.reps}"
                cue_shown_at = now
                if args.no_display:
                    print(f"[{now:6.2f}s] cue {next_cue + 1}/{len(plan)}: {current_text}")
                next_cue += 1

            # 3. Show the cue screen (a copy of the frame, so the saved video stays clean)
            if not args.no_display:
                display = frame.copy()
                if now < args.countdown:
                    draw_big_text(display, f"GET READY  {int(args.countdown - now) + 1}", height // 2, YELLOW)
                elif next_cue < len(plan) and (next_cue == 0 or plan[next_cue]["punch"] != plan[next_cue - 1]["punch"]
                                               or plan[next_cue]["hand"] != plan[next_cue - 1]["hand"]) \
                        and now - cue_shown_at > args.interval * 0.8 and not args.shuffle:
                    # Rest between blocks: tell the athlete what's coming next
                    draw_big_text(display, "NEXT: " + plan[next_cue]["text"], height // 2, YELLOW, scale=1.2)
                elif current_text:
                    # Flash green for the first 0.4 s of each cue = "throw now"
                    colour = GREEN if now - cue_shown_at < 0.4 else WHITE
                    draw_big_text(display, current_text, height // 2, colour)
                draw_big_text(display, f"REC  {now:5.1f}s   {next_cue}/{len(plan)}", 40, (0, 0, 255),
                              scale=0.7, thickness=2)
                cv2.imshow("Southpaw OS - clip recorder (Q to stop)", display)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    print("Stopped early by user")
                    break

            frame_index += 1
            if now >= end_time:
                break
    except KeyboardInterrupt:
        print("Stopped early (Ctrl+C)")
    finally:
        duration = (frame_index / cam_fps) if is_file else (time.monotonic() - start)
        cap.release()
        writer.release()
        labels_file.close()
        frames_file.close()
        cv2.destroyAllWindows()

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
            "frames_recorded": frame_index,
            "duration_s": round(duration, 2),
            "measured_fps": round(frame_index / duration, 2) if duration > 0 else 0,
            "cues_shown": next_cue,
            "reps": args.reps, "interval_s": args.interval, "shuffle": args.shuffle,
        }
        with open(os.path.join(session_dir, "meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

        print(f"\nSaved {frame_index} frames ({duration:.1f} s, {meta['measured_fps']} fps) "
              f"and {next_cue} labelled punches to {session_dir}/")
        if meta["measured_fps"] and meta["measured_fps"] < cam_fps * 0.9:
            print("WARNING: recorded noticeably slower than the camera - frames were dropped. "
                  "Close other programs, or try --no-display, and check the lighting (dim light lowers webcam fps).")


if __name__ == "__main__":
    main()