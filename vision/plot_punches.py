"""
plot_punches.py - Southpaw OS, computer vision (Member D)

Draws every labelled punch in a session so you can SEE how your jabs, hooks and uppercuts
differ before (or while) tuning the detector. Also good material for the video.

For each cue in labels.csv it takes the next ~1.2 s of the cued hand and plots:
  * top row:    the wrist's path relative to the shoulder (body lengths; up is up),
                starting from where it was when the cue appeared. One panel per hand.
  * bottom row: the 3D elbow angle over time after the cue (180 = straight arm).

Colours: blue = straight (jab/cross), orange = hook, green = uppercut.

Uses landmarks.csv (every frame, smoother) if it exists, else landmarks_9fps.csv.

Examples (inside the venv):
  python plot_punches.py recordings/test_left
  python plot_punches.py recordings/test_left --landmarks landmarks_9fps.csv
Saves punch_paths.png in the session folder.
"""

import argparse
import csv
import json
import math
import os

from punch_detector import pose_from_csv_row

COLOURS = {"straight": "tab:blue", "hook": "tab:orange", "uppercut": "tab:green"}


def kind_of(punch):
    return "straight" if punch in ("jab", "cross") else punch


def angle3d(s, e, w):
    """3D elbow angle (degrees) from world points, or None."""
    if None in (s.wx, e.wx, w.wx):
        return None
    v1 = (s.wx - e.wx, s.wy - e.wy, s.wz - e.wz)
    v2 = (w.wx - e.wx, w.wy - e.wy, w.wz - e.wz)
    n = math.sqrt(sum(a * a for a in v1)) * math.sqrt(sum(a * a for a in v2))
    if n == 0:
        return None
    return math.degrees(math.acos(max(-1, min(1, sum(a * b for a, b in zip(v1, v2)) / n))))


def main():
    parser = argparse.ArgumentParser(description="Plot the wrist path of every labelled punch")
    parser.add_argument("session")
    parser.add_argument("--landmarks", help="landmark file name (default: landmarks.csv, else landmarks_9fps.csv)")
    parser.add_argument("--window", type=float, default=1.2, help="seconds after each cue to plot")
    args = parser.parse_args()

    import matplotlib
    matplotlib.use("Agg")             # draw to a file; works over SSH
    import matplotlib.pyplot as plt

    session = args.session.rstrip("/\\")
    name = args.landmarks or ("landmarks.csv" if os.path.exists(os.path.join(session, "landmarks.csv"))
                              else "landmarks_9fps.csv")
    path = os.path.join(session, name)
    width, height = 640, 480
    info_path = path.replace(".csv", "_info.json")
    if os.path.exists(info_path):
        with open(info_path) as f:
            info = json.load(f)
        width, height = info.get("width", width), info.get("height", height)

    with open(os.path.join(session, "labels.csv")) as f:
        labels = list(csv.DictReader(f))
    with open(path) as f:
        frames = [pose_from_csv_row(r, width, height) for r in csv.DictReader(f)]

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    for col, hand in enumerate(("L", "R")):
        path_ax, angle_ax = axes[0][col], axes[1][col]
        for cue in labels:
            if cue["hand"] != hand:
                continue
            t0 = float(cue["cue_time_s"])
            xs, ys, ts, angles = [], [], [], []
            for t, pose in frames:
                if not (t0 <= t <= t0 + args.window) or pose is None:
                    continue
                s, e, w = pose.get(f"{hand}_SHOULDER"), pose.get(f"{hand}_ELBOW"), pose.get(f"{hand}_WRIST")
                lh, rh = pose.get("L_HIP"), pose.get("R_HIP")
                ls, rs = pose.get("L_SHOULDER"), pose.get("R_SHOULDER")
                if None in (s, e, w, lh, rh, ls, rs) or w.vis < 0.5:
                    continue
                # Body length = shoulders-midpoint to hips-midpoint, in pixels
                body = math.hypot((ls.x + rs.x - lh.x - rh.x) / 2, (ls.y + rs.y - lh.y - rh.y) / 2) or 1
                xs.append((w.x - s.x) / body)
                ys.append(-(w.y - s.y) / body)          # minus: so "up" is up on the chart
                ts.append(t - t0)
                angles.append(angle3d(s, e, w))
            if len(xs) < 2:
                continue
            colour = COLOURS[kind_of(cue["punch"])]
            # Start each path from where the hand was when the cue appeared
            path_ax.plot([x - xs[0] for x in xs], [y - ys[0] for y in ys], color=colour, alpha=0.5, lw=1)
            path_ax.plot(0, 0, "k.", ms=3)
            pts = [(tt, a) for tt, a in zip(ts, angles) if a is not None]
            if pts:
                angle_ax.plot([p[0] for p in pts], [p[1] for p in pts], color=colour, alpha=0.5, lw=1)

        path_ax.set_title(f"{'Left' if hand == 'L' else 'Right'} hand: wrist path from guard")
        path_ax.set_xlabel("sideways in the image (body lengths)")
        path_ax.set_ylabel("up / down (body lengths)")
        path_ax.set_aspect("equal", adjustable="datalim")
        path_ax.grid(alpha=0.3)
        angle_ax.set_title(f"{'Left' if hand == 'L' else 'Right'} hand: 3D elbow angle after the cue")
        angle_ax.set_xlabel("seconds after cue")
        angle_ax.set_ylabel("elbow angle (degrees, 180 = straight)")
        angle_ax.grid(alpha=0.3)

    for k, c in COLOURS.items():
        axes[0][0].plot([], [], color=c, label=k)
    axes[0][0].legend(loc="best")
    fig.suptitle(f"{os.path.basename(session)}  ({name})")
    fig.tight_layout()
    out = os.path.join(session, "punch_paths.png")
    fig.savefig(out, dpi=110)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
