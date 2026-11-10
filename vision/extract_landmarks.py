"""
extract_landmarks.py - Southpaw OS, computer vision (Member D)

Runs the MediaPipe pose model over a recorded session ONCE and saves the body points
of every processed frame to a CSV file. The punch detector and evaluate.py then read
that CSV, so tuning thresholds takes seconds instead of re-running the slow pose model.

Input:  a session folder made by record_clips.py (needs video.avi and frames.csv)
Output (in the same folder):
  landmarks.csv          one row per processed frame: time + x/y/z/visibility of each body point
                         (image coordinates) + wx/wy/wz (3D "world" coordinates in metres)
  landmarks_info.json    how the file was made (model, frame size, which frames were used)

Two modes:
  * All frames (default): every recorded frame is processed. Best for looking at punch shapes.
  * --sim-fps 9: only the frames the Pi would really get live at ~9 fps (it skips the rest
    while it is busy). The pose tracker behaves differently when frames are further apart,
    so tune and evaluate the detector on THIS version. Saved as landmarks_9fps.csv.

Examples (inside the venv):
  python extract_landmarks.py recordings/test_left
  python extract_landmarks.py recordings/test_left --sim-fps 9
  python extract_landmarks.py recordings/test_left recordings/test_right --sim-fps 9
"""

import argparse
import csv
import json
import os
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions, vision

# Body points to save (MediaPipe numbers 33; these are the ones useful for punches).
# "L"/"R" are the PERSON's left/right, not the image's.
POINTS = {
    "NOSE": 0,
    "L_SHOULDER": 11, "R_SHOULDER": 12,
    "L_ELBOW": 13, "R_ELBOW": 14,
    "L_WRIST": 15, "R_WRIST": 16,
    "L_PINKY": 17, "R_PINKY": 18,
    "L_INDEX": 19, "R_INDEX": 20,     # index knuckle area = roughly where the fist is
    "L_THUMB": 21, "R_THUMB": 22,
    "L_HIP": 23, "R_HIP": 24,
}


def csv_header():
    """Column names: frame info, then 7 numbers per body point."""
    cols = ["frame", "time_s", "detected"]
    for name in POINTS:
        # x, y: position as a fraction of image width/height (0-1)
        # z: rough depth relative to the hips; vis: how likely the point is visible (0-1)
        # wx, wy, wz: 3D position in metres, origin between the hips (MediaPipe "world landmarks")
        cols += [f"{name}_x", f"{name}_y", f"{name}_z", f"{name}_vis",
                 f"{name}_wx", f"{name}_wy", f"{name}_wz"]
    return cols


def read_frame_times(session_dir):
    """Load the real capture time of every frame (written by record_clips.py)."""
    times = []
    with open(os.path.join(session_dir, "frames.csv")) as f:
        for row in csv.DictReader(f):
            times.append(float(row["time_s"]))
    return times


def pick_frames_like_the_pi(frame_times, sim_fps):
    """
    Work out which frames the Pi would actually process live at sim_fps.

    Live, the camera thread always holds the NEWEST frame. Each time the Pi finishes a frame
    (after 1/sim_fps seconds of work), it grabs whatever frame is newest at that moment;
    everything captured in between is never seen. If no new frame has arrived yet, it waits
    for the next one.
    """
    busy = 1.0 / sim_fps
    chosen = []
    last = -1                      # index of the last processed frame
    free_at = frame_times[0]       # time the Pi is ready for its next frame
    j = 0
    n = len(frame_times)
    while True:
        # Newest frame captured by the time the Pi is free
        while j + 1 < n and frame_times[j + 1] <= free_at:
            j += 1
        if j <= last:              # nothing new yet: wait for the next frame to arrive
            j = last + 1
            if j >= n:
                break
            free_at = frame_times[j]
        chosen.append(j)
        last = j
        free_at += busy            # the Pi is busy with this frame for 1/sim_fps seconds
    return set(chosen)


def extract(session_dir, model_path, sim_fps):
    video_path = os.path.join(session_dir, "video.avi")
    if not os.path.exists(video_path):
        print(f"Skipping {session_dir}: no video.avi")
        return
    frame_times = read_frame_times(session_dir)

    out_name = f"landmarks_{sim_fps:g}fps.csv" if sim_fps else "landmarks.csv"
    out_path = os.path.join(session_dir, out_name)

    # A fresh pose model per clip, in VIDEO mode (it tracks the person from frame to frame,
    # exactly like the live system does)
    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(options)

    video = cv2.VideoCapture(video_path)
    width = int(video.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))

    # In --sim-fps mode, only process the frames the Pi would really have picked up live
    wanted = pick_frames_like_the_pi(frame_times, sim_fps) if sim_fps else None

    processed = detected_count = 0
    wrist_seen = {"L_WRIST": 0, "R_WRIST": 0}
    last_ts_ms = -1
    start = time.monotonic()

    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(csv_header())

        frame_index = 0
        while True:
            ok, frame = video.read()
            if not ok:
                break
            # Real capture time of this frame (falls back to 30 fps spacing if frames.csv is short)
            t = frame_times[frame_index] if frame_index < len(frame_times) else frame_index / 30.0

            # Simulated Pi: skip frames it would have missed while busy
            if wanted is not None and frame_index not in wanted:
                frame_index += 1
                continue

            # MediaPipe needs RGB colour order and strictly increasing timestamps (ms)
            ts_ms = max(int(t * 1000), last_ts_ms + 1)
            last_ts_ms = ts_ms
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = landmarker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts_ms)

            row = [frame_index, f"{t:.4f}"]
            if result.pose_landmarks:
                lms = result.pose_landmarks[0]
                world = result.pose_world_landmarks[0] if result.pose_world_landmarks else None
                row.append(1)
                detected_count += 1
                for name, idx in POINTS.items():
                    p = lms[idx]
                    w = world[idx] if world else None
                    row += [f"{p.x:.5f}", f"{p.y:.5f}", f"{p.z:.5f}", f"{p.visibility:.3f}",
                            f"{w.x:.5f}" if w else "", f"{w.y:.5f}" if w else "", f"{w.z:.5f}" if w else ""]
                    if name in wrist_seen and p.visibility >= 0.5:
                        wrist_seen[name] += 1
            else:
                # Nobody found in this frame: keep the row (so gaps are visible) but leave points empty
                row.append(0)
                row += [""] * (7 * len(POINTS))
            writer.writerow(row)

            processed += 1
            frame_index += 1
            if processed % 100 == 0:
                print(f"  {session_dir}: {processed} frames processed...")

    video.release()
    landmarker.close()
    elapsed = time.monotonic() - start

    info = {
        "source_video": "video.avi",
        "model": os.path.basename(model_path),
        "mediapipe_version": mp.__version__,
        "width": width, "height": height,
        "mode": f"simulated {sim_fps:g} fps (frames skipped while busy)" if sim_fps else "all frames",
        "frames_in_video": frame_index,
        "frames_processed": processed,
        "frames_with_person": detected_count,
        "points": POINTS,
    }
    with open(os.path.join(session_dir, out_name.replace(".csv", "_info.json")), "w") as f:
        json.dump(info, f, indent=2)

    # Quick quality summary: how often was the person / each wrist actually found?
    def pct(n):
        return f"{100 * n / processed:.0f}%" if processed else "-"
    print(f"\n{session_dir} -> {out_name}")
    print(f"  frames processed: {processed} of {frame_index}  ({processed / elapsed:.1f} fps on this machine)")
    print(f"  person found:     {pct(detected_count)}")
    print(f"  left wrist seen:  {pct(wrist_seen['L_WRIST'])}   right wrist seen: {pct(wrist_seen['R_WRIST'])}")


def main():
    parser = argparse.ArgumentParser(description="Extract pose landmarks from recorded sessions")
    parser.add_argument("sessions", nargs="+", help="session folder(s), e.g. recordings/test_left")
    parser.add_argument("--model", default="pose_landmarker_lite.task")
    parser.add_argument("--sim-fps", type=float, default=0,
                        help="simulate the Pi's live speed (e.g. 9); 0 = process every frame")
    args = parser.parse_args()

    for session in args.sessions:
        extract(session.rstrip("/\\"), args.model, args.sim_fps)


if __name__ == "__main__":
    main()