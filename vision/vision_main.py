"""
vision_main.py - Southpaw OS, computer vision (Member D)

The live vision process:  webcam -> pose model -> punch detector -> contact check -> hub

  * sends a JSON event to the hub (UDP, see sender.py) for every punch it sees
  * sends a heartbeat every second (fps, person in view, camera OK)
  * optional overlay window / recording (skeleton, wrist trails, bag zone, last punch)
  * keeps running if the camera is unplugged (reconnects) or the hub isn't running
  * runs on its own: the game must work with this process off

Examples (inside the venv, in the vision folder):
  python vision_main.py                                   # webcam, window (on the Pi desktop)
  python vision_main.py --no-display                      # webcam, no window (over SSH / for the demo)
  python vision_main.py --source recordings/test_left/video.avi          # replay a clip in real time
  python vision_main.py --source recordings/test_left/video.avi --fast   # every frame, as fast as possible
  python vision_main.py --record demo_overlay.mp4         # also save the overlay video (for the YouTube video)
  python vision_main.py --set output.hub_host=192.168.1.101   # send to a hub on another machine

Test without the hub: run  python udp_listener.py  in a second terminal first.
"""

import argparse
import collections
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

from camera import open_source
from contact import ContactChecker
from punch_detector import PunchDetector, pose_from_mediapipe
from sender import HubSender
from vision_config import load_config

# Lines of the upper-body skeleton for the overlay
SKELETON = [("L_SHOULDER", "R_SHOULDER"), ("L_SHOULDER", "L_ELBOW"), ("L_ELBOW", "L_WRIST"),
            ("L_WRIST", "L_INDEX"), ("R_SHOULDER", "R_ELBOW"), ("R_ELBOW", "R_WRIST"),
            ("R_WRIST", "R_INDEX"), ("L_SHOULDER", "L_HIP"), ("R_SHOULDER", "R_HIP"), ("L_HIP", "R_HIP")]
COLOUR = {"L": (255, 160, 0), "R": (0, 140, 255), "body": (200, 200, 200)}   # BGR: blue-ish, orange, grey


def draw_overlay(frame, pose, trails, stats, last_event, contact_checker, min_vis):
    """Skeleton, wrist trails, bag zone, speed numbers and the last punch, drawn onto the frame."""
    contact_checker.draw(frame)
    if pose is not None:
        for a, b in SKELETON:
            pa, pb = pose.get(a), pose.get(b)
            if pa is None or pb is None or pa.vis < min_vis or pb.vis < min_vis:
                continue
            # Arm lines in the arm's colour, torso lines grey
            colour = COLOUR[b[0]] if b.endswith(("ELBOW", "WRIST", "INDEX")) else COLOUR["body"]
            cv2.line(frame, (int(pa.x), int(pa.y)), (int(pb.x), int(pb.y)), colour, 3)
    for side, trail in trails.items():
        pts = list(trail)
        for i in range(1, len(pts)):
            cv2.line(frame, pts[i - 1], pts[i], COLOUR[side], max(1, int(6 * i / len(pts))))

    lines = [f"{stats['fps']:.1f} fps | pose {stats['infer_ms']:.0f} ms",
             f"frame age {stats['age_ms']:.0f} ms",
             "person: yes" if pose is not None else "person: NOT FOUND",
             f"hub: sent {stats['sent']}" + (f", {stats['errors']} errors" if stats["errors"] else "")]
    cv2.rectangle(frame, (0, 0), (250, 22 + 22 * len(lines)), (0, 0, 0), -1)
    for i, text in enumerate(lines):
        cv2.putText(frame, text, (8, 24 + 22 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    if last_event and time.monotonic() - last_event["shown_at"] < 1.5:
        text = last_event["text"]
        (tw, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.2, 3)
        x, y = (frame.shape[1] - tw) // 2, frame.shape[0] - 30
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 9, cv2.LINE_AA)
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, last_event["colour"], 3, cv2.LINE_AA)
    return frame


def main():
    parser = argparse.ArgumentParser(description="Southpaw OS live vision: punches -> hub")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.NAME=VALUE",
                        help="override a setting for this run (can repeat)")
    parser.add_argument("--source", help="webcam number/path or a video file (default: camera.source)")
    parser.add_argument("--fast", action="store_true", help="for clips: every frame, as fast as possible")
    parser.add_argument("--no-display", action="store_true", help="no window (SSH / demo)")
    parser.add_argument("--record", help="save the overlay video to this .mp4 file")
    parser.add_argument("--no-send", action="store_true", help="don't send anything to the hub")
    parser.add_argument("--quiet", action="store_true", help="don't print each punch")
    args = parser.parse_args()

    config = load_config(args.config, args.set)
    cam_cfg, out_cfg = config["camera"], config["output"]
    show = config["debug"]["show_window"] and not args.no_display
    record_path = args.record or config["debug"]["record_path"]
    min_conf = config["classifier"]["min_confidence_to_send"]
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
    detector = PunchDetector(config)
    contact = ContactChecker(config, (width, height))
    sender = HubSender(out_cfg["hub_host"], out_cfg["hub_port"], enabled=out_cfg["enabled"] and not args.no_send)
    print(f"Bag zone: {'calibrated' if contact.calibrated else 'NOT calibrated (contact will be null)'}")
    print(f"Hub: {'udp://%s:%d' % sender.address if sender.enabled else 'not sending'}  "
          f"| stance {config['player']['stance']} | motion {config['tracker']['motion']}")

    trails = {s: collections.deque(maxlen=config["debug"]["trail_length"]) for s in "LR"}
    stats = {"fps": 0.0, "infer_ms": 0.0, "age_ms": 0.0, "sent": 0, "errors": 0}
    last_event, writer = None, None
    last_id, last_ts_ms, frames_done = 0, -1, 0
    loop_start = last_beat = time.monotonic()
    camera_ok, last_t = True, 0.0

    try:
        while True:
            got = source.read(last_id, timeout=1.0)
            now = time.monotonic()
            if got is None:
                if source.ended:
                    print("End of clip")
                    break
                camera_ok = False                 # no frame for a second: camera trouble
                pose = None
            else:
                camera_ok = True
                last_id, frame, t_capture = got
                last_t = t_capture
                # MediaPipe VIDEO mode needs strictly increasing whole-millisecond timestamps
                ts_ms = max(int(t_capture * 1000), last_ts_ms + 1)
                last_ts_ms = ts_ms

                # --- Pose model ---
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                t0 = time.monotonic()
                result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)
                infer_ms = (time.monotonic() - t0) * 1000
                pose = pose_from_mediapipe(result, width, height)

                # --- Punches ---
                for event in detector.update(t_capture, pose):
                    hit = contact.check(event)
                    if event.conf < min_conf:
                        if not args.quiet:
                            print(f"  (ignored low-confidence {event.hand} {event.type}, conf {event.conf:.2f})")
                        continue
                    message = sender.punch(event, hit)
                    if not args.quiet:
                        print(f"PUNCH {event.hand} {event.type:<8} conf {event.conf:.2f}  contact {hit}  "
                              f"age {message['age_ms']} ms")
                    last_event = {"text": f"{event.hand} {event.type.upper()}" + (" - HIT" if hit else ""),
                                  "colour": (0, 220, 0) if hit else (255, 255, 255), "shown_at": time.monotonic()}

                # --- Speed numbers (smoothed) ---
                frames_done += 1
                elapsed = now - loop_start
                fps_now = frames_done / elapsed if elapsed > 0 else 0.0
                stats["fps"] = fps_now if frames_done < 10 else 0.9 * stats["fps"] + 0.1 * fps_now
                stats["infer_ms"] = infer_ms if frames_done == 1 else 0.9 * stats["infer_ms"] + 0.1 * infer_ms
                stats["age_ms"] = (time.monotonic() - t_capture) * 1000 if not args.fast else 0.0
                stats["sent"], stats["errors"] = sender.sent, sender.errors

                # --- Overlay (only drawn if someone will see it) ---
                if show or record_path:
                    for side in "LR":
                        w = pose.get(f"{side}_WRIST") if pose else None
                        if w is not None and w.vis >= min_vis:
                            trails[side].append((int(w.x), int(w.y)))
                        else:
                            trails[side].clear()
                    draw_overlay(frame, pose, trails, stats, last_event, contact, min_vis)
                    if record_path:
                        if writer is None:
                            writer = cv2.VideoWriter(record_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                                     max(5.0, round(stats["fps"]) or 9.0), (width, height))
                        writer.write(frame)
                    if show:
                        cv2.imshow("Southpaw OS - vision (Q to quit)", frame)
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
        for event in detector.flush(last_t):          # a punch still in progress at the very end
            sender.punch(event, contact.check(event))
        total = time.monotonic() - loop_start
        print(f"\nProcessed {frames_done} frames in {total:.1f} s ({frames_done / total:.1f} fps); "
              f"sent {sender.sent} messages, {sender.errors} send errors")
        source.close()
        landmarker.close()
        sender.close()
        if writer:
            writer.release()
            print(f"Saved overlay video to {record_path}")
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
