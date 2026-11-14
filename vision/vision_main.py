"""
vision_main.py - Southpaw OS, computer vision (Member D)

The live vision process:  webcam -> pose model -> guard check -> hub

  * checks every processed frame (~9 a second) whether each hand is up in guard
    (guard.py) and sends that to the hub (UDP, see sender.py); the hub works out the
    round's guard % and drops, leaving out the moments the wrist boards saw a punch
  * keeps its own running guard % / drop count for the overlay and the summary at the end
    (without the hub it can't tell punches apart, so those numbers include punches)
  * sends a heartbeat every second (fps, person in view, camera OK)
  * optional overlay window / video (skeleton, guard lines, fists in green/red, "HANDS UP!")
  * keeps running if the camera is unplugged (reconnects) or the hub isn't running
  * runs on its own: the game must work with this process off

Examples (inside the venv, in the vision folder):
  python vision_main.py                          # webcam 0 with a window
  python vision_main.py --source 1               # webcam 1 (the C922 on a laptop/desktop with a built-in camera)
  python vision_main.py --no-display             # no window (over SSH / for the demo)
  python vision_main.py --record-raw recordings/guard1     # also save the plain camera video, to replay later
  python vision_main.py --source recordings/guard1/video.avi   # replay a saved clip in real time
  python vision_main.py --source recordings/guard1/video.avi --fast --no-display   # as fast as possible
  python vision_main.py --record overlay.mp4     # save the overlay video (for the YouTube video)
  python vision_main.py --set guard.rear_max_below_mouth=0.2   # try a different setting once

Test without the hub: run  python udp_listener.py  in a second terminal first.
"""

import argparse
import csv
import os
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

from camera import open_source
from guard import GuardChecker, GuardTracker
from pose import pose_from_mediapipe
from sender import HubSender
from vision_config import load_config

# Lines of the upper-body skeleton for the overlay
SKELETON = [("L_SHOULDER", "R_SHOULDER"), ("L_SHOULDER", "L_ELBOW"), ("L_ELBOW", "L_WRIST"),
            ("R_SHOULDER", "R_ELBOW"), ("R_ELBOW", "R_WRIST"), ("L_SHOULDER", "L_HIP"),
            ("R_SHOULDER", "R_HIP"), ("L_HIP", "R_HIP")]
# Colours are BGR (OpenCV's order)
GREEN, RED, GREY, WHITE, BLACK = (0, 200, 0), (0, 0, 230), (150, 150, 150), (255, 255, 255), (0, 0, 0)
HAND_NAME = {"L": "left", "R": "right"}


def state_colour(up):
    return GREEN if up else (RED if up is False else GREY)


def draw_overlay(frame, pose, hands, tracker, checker, stats, min_vis):
    """Skeleton, each hand's guard line, the fists, and a panel with the numbers."""
    if pose is not None:
        for a, b in SKELETON:
            pa, pb = pose[a], pose[b]
            if pa.vis >= min_vis and pb.vis >= min_vis:
                cv2.line(frame, (int(pa.x), int(pa.y)), (int(pb.x), int(pb.y)), GREY, 2)
    for side, h in hands.items():
        colour = state_colour(h.up)
        # The hand's limit line: the fist must stay above it. Drawn across the face area.
        if h.limit_y is not None and pose is not None and checker.scale:
            cx = pose["NOSE"].x
            half = checker.cfg["max_side_reach"] * checker.scale
            y = int(h.limit_y)
            cv2.line(frame, (int(cx - half), y), (int(cx + half), y), colour, 1, cv2.LINE_AA)
            cv2.putText(frame, side, (int(cx + half) + 4, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1)
        if h.fist is not None:
            cv2.circle(frame, (int(h.fist[0]), int(h.fist[1])), 12, colour, 3)

    def pct(side):
        p = tracker.pct(side)
        return "--" if p is None else f"{p:.0f}%"

    lead = checker.lead
    lines = [f"{stats['fps']:.1f} checks/s | pose {stats['infer_ms']:.0f} ms",
             "person: yes" if pose is not None else "person: NOT FOUND",
             f"guard (both hands): {pct(None)}",
             f"lead {lead}: {pct(lead)}  rear {'R' if lead == 'L' else 'L'}: {pct('R' if lead == 'L' else 'L')}",
             f"drops: {tracker.hands['L'].drops + tracker.hands['R'].drops}",
             f"hub: sent {stats['sent']}" + (f", {stats['errors']} errors" if stats["errors"] else "")]
    cv2.rectangle(frame, (0, 0), (260, 22 + 22 * len(lines)), BLACK, -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (8, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 1, cv2.LINE_AA)

    # Big warning while a hand is in a counted drop
    if tracker.is_dropped("L") or tracker.is_dropped("R"):
        text = "HANDS UP!"
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.6, 4)
        x, y = (frame.shape[1] - tw) // 2, frame.shape[0] - 30
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.6, BLACK, 10, cv2.LINE_AA)
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.6, RED, 4, cv2.LINE_AA)
    return frame


def open_video_writer(path, fps, size):
    """An .mp4 writer. Tries H.264 first (plays everywhere: phones, WhatsApp, Telegram, Windows);
    falls back to MPEG-4 'mp4v' (always available in OpenCV, but some players can't open it)."""
    for codec in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    raise SystemExit(f"Could not create the video file {path}")


class RawRecorder:
    """Saves the plain camera frames (video.avi + frames.csv with capture times) into a folder,
    so a session can be replayed later with --source <folder>/video.avi to try other settings."""

    def __init__(self, folder, size, fps):
        if os.path.exists(os.path.join(folder, "video.avi")):
            raise SystemExit(f"{folder}/video.avi already exists - pick a new folder name")
        os.makedirs(folder, exist_ok=True)
        self.writer = cv2.VideoWriter(os.path.join(folder, "video.avi"), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
        self.times = open(os.path.join(folder, "frames.csv"), "w", newline="")
        self.csv = csv.writer(self.times)
        self.csv.writerow(["frame", "time_s"])
        self.count, self.start = 0, None

    def write(self, frame, t_capture):
        if self.start is None:
            self.start = t_capture
        self.writer.write(frame)
        self.csv.writerow([self.count, f"{t_capture - self.start:.4f}"])
        self.count += 1

    def close(self):
        self.writer.release()
        self.times.close()


def main():
    parser = argparse.ArgumentParser(description="Southpaw OS live vision: guard tracking -> hub")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.NAME=VALUE",
                        help="override a setting for this run (can repeat)")
    parser.add_argument("--source", help="webcam number/path or a video file (default: camera.source)")
    parser.add_argument("--fast", action="store_true", help="for clips: as fast as possible, not real time")
    parser.add_argument("--no-display", action="store_true", help="no window (SSH / demo)")
    parser.add_argument("--record", help="save the overlay video to this .mp4 file")
    parser.add_argument("--record-raw", metavar="FOLDER", help="save the plain camera video to FOLDER (to replay later)")
    parser.add_argument("--no-send", action="store_true", help="don't send anything to the hub")
    parser.add_argument("--quiet", action="store_true", help="don't print drops")
    args = parser.parse_args()

    config = load_config(args.config, args.set)
    cam_cfg, out_cfg, guard_cfg = config["camera"], config["output"], config["guard"]
    show = config["debug"]["show_window"] and not args.no_display
    record_path = args.record or config["debug"]["record_path"]
    print_changes = config["debug"]["print_changes"] and not args.quiet
    min_vis = config["pose"]["min_visibility"]

    source = open_source(args.source or cam_cfg["source"], cam_cfg["width"], cam_cfg["height"],
                         cam_cfg["fps"], realtime=not args.fast)
    width, height = source.size()
    print(f"Source: {source.describe()}")

    landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=config["pose"]["model_path"]),
        running_mode=vision.RunningMode.VIDEO,      # tracks the person frame to frame
        num_poses=1,
        min_pose_detection_confidence=config["pose"]["min_detection_confidence"],
        min_pose_presence_confidence=config["pose"]["min_presence_confidence"],
        min_tracking_confidence=config["pose"]["min_tracking_confidence"],
    ))
    checker = GuardChecker(config)
    tracker = GuardTracker(drop_hold_s=guard_cfg["drop_hold_s"])
    sender = HubSender(out_cfg["hub_host"], out_cfg["hub_port"], enabled=out_cfg["enabled"] and not args.no_send)
    raw = RawRecorder(args.record_raw, (width, height), cam_cfg["fps"]) if args.record_raw else None
    print(f"Hub: {'udp://%s:%d' % sender.address if sender.enabled else 'not sending'}  "
          f"| stance {config['player']['stance']} (lead hand {checker.lead})")

    # Only run the pose model process_fps times a second (like the Pi); 0 = every frame
    process_every = 1.0 / cam_cfg["process_fps"] if cam_cfg.get("process_fps") else 0.0
    next_due = 0.0
    stats = {"fps": 0.0, "infer_ms": 0.0, "sent": 0, "errors": 0}
    writer = None
    last_id, last_ts_ms, frames_done = 0, -1, 0
    loop_start = last_beat = time.monotonic()
    t_first = None                         # capture time of the first frame (printed times count from it)
    camera_ok, pose = True, None

    try:
        while True:
            got = source.read(last_id, timeout=1.0)
            now = time.monotonic()
            if got is None:
                if source.ended:
                    print("End of clip")
                    break
                camera_ok, pose = False, None          # no frame for a second: camera trouble
            else:
                camera_ok = True
                last_id, frame, t_capture = got
                if t_first is None:
                    t_first = t_capture
                if raw:
                    raw.write(frame, t_capture)        # every camera frame, before skipping any

                # --- Skip frames until the next check is due (process_fps) ---
                if process_every:
                    if t_capture < next_due - 0.015:   # not due yet (0.015 s = half a frame at 30 fps)
                        continue
                    next_due = (next_due + process_every if next_due + process_every > t_capture
                                else t_capture + process_every)

                # --- Pose model (MediaPipe VIDEO mode needs strictly increasing whole-ms timestamps) ---
                ts_ms = max(int(t_capture * 1000), last_ts_ms + 1)
                last_ts_ms = ts_ms
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                t0 = time.monotonic()
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)
                infer_ms = (time.monotonic() - t0) * 1000
                pose = pose_from_mediapipe(result, width, height)

                # --- Guard check, running stats, message to the hub ---
                hands = checker.check(pose)
                was_dropped = {s: tracker.is_dropped(s) for s in "LR"}
                new_drops = tracker.update(t_capture, {s: h.up for s, h in hands.items()})
                sender.guard(t_capture, hands)
                if print_changes:
                    for s in new_drops:
                        role = "lead" if s == checker.lead else "rear"
                        print(f"[{t_capture - t_first:7.1f} s] DROP: {HAND_NAME[s]} ({role}) hand down")
                    for s in "LR":
                        if was_dropped[s] and not tracker.is_dropped(s) and hands[s].up:
                            print(f"[{t_capture - t_first:7.1f} s]       {HAND_NAME[s]} hand back up")

                # --- Speed numbers (smoothed) ---
                # fps = guard checks per second of VIDEO time (capture times), not of wall-clock time:
                # live, that's the real rate (lower if the Pi can't keep up); when replaying a clip
                # with --fast it's still the rate the Pi would get, not how fast this PC replays.
                frames_done += 1
                video_s = t_capture - t_first
                fps_now = (frames_done - 1) / video_s if video_s > 0 else 0.0
                stats["fps"] = fps_now if frames_done < 10 else 0.9 * stats["fps"] + 0.1 * fps_now
                stats["infer_ms"] = infer_ms if frames_done == 1 else 0.9 * stats["infer_ms"] + 0.1 * infer_ms
                stats["sent"], stats["errors"] = sender.sent, sender.errors

                # --- Overlay (only drawn if someone will see it) ---
                if show or record_path:
                    draw_overlay(frame, pose, hands, tracker, checker, stats, min_vis)
                    if record_path:
                        if writer is None:   # one overlay frame per check, so play it back at the check rate
                            out_fps = cam_cfg["process_fps"] or cam_cfg["fps"]
                            writer = open_video_writer(record_path, out_fps, (width, height))
                        writer.write(frame)
                    if show:
                        cv2.imshow("Southpaw OS - guard (Q to quit)", frame)
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break

            # --- Heartbeat ---
            if now - last_beat >= out_cfg["heartbeat_interval_s"]:
                last_beat = now
                sender.status(stats["fps"], pose is not None, camera_ok)
                if not camera_ok:
                    print("No frames from the camera - waiting for it to come back...")
    except KeyboardInterrupt:
        pass
    finally:
        total = time.monotonic() - loop_start
        s = tracker.summary(checker.lead)

        def pct(value):
            return "--" if value is None else f"{value}%"     # "--" = never saw that hand

        print(f"\nProcessed {frames_done} frames in {total:.1f} s ({frames_done / max(total, 1e-6):.1f} fps); "
              f"sent {sender.sent} messages, {sender.errors} send errors")
        print(f"Guard (punches included): both hands {pct(s['guard_pct'])}, lead {pct(s['guard_pct_lead'])}, "
              f"rear {pct(s['guard_pct_rear'])}, drops {s['guard_drops']}")
        source.close()
        landmarker.close()
        sender.close()
        if raw:
            raw.close()
            print(f"Saved the camera video to {args.record_raw}/video.avi ({raw.count} frames)")
        if writer:
            writer.release()
            print(f"Saved the overlay video to {record_path}")
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
