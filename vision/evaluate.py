"""
evaluate.py - Southpaw OS, computer vision (Member D)

How good is the punch detector? Replays recorded sessions through it and compares what
it detected with what was actually thrown (the cues logged by record_clips.py).

Needs, in each session folder:
  labels.csv              from record_clips.py (which punch was cued, and when)
  landmarks_9fps.csv      from extract_landmarks.py --sim-fps 9 (or pick another with --landmarks)

Prints, per session and overall:
  * detection rate      - how many thrown punches were detected at all
  * hand accuracy       - of those, how many got the right hand
  * overall accuracy    - right hand AND right punch type, out of everything thrown
  * extra detections    - "punches" detected that weren't thrown (false alarms)
  * a confusion matrix  - e.g. "L uppercut was called L hook 3 times"
  * suggested thresholds - the values that would best separate the punch types in THIS data

Also saves:
  features.csv     every detection with its measurements and the true label (for tuning,
                   or for training a decision tree later, like the wrist boards)
  evaluation.png   (with --plot) the confusion matrix + a chart of the two key measurements

Jab vs cross is just "straight punch" + which hand, so punches are compared as
hand + straight/hook/uppercut. That also keeps southpaw sessions correct.

Examples (inside the venv):
  python evaluate.py recordings/test_left
  python evaluate.py recordings/test_left recordings/test_right --plot
  python evaluate.py recordings/test_left --set classifier.straight_min_elbow_deg=140
  python evaluate.py recordings/test_left --set classifier.use_world_angles=false   # try 2D angles
"""

import argparse
import csv
import json
import os

from punch_detector import PunchDetector, pose_from_csv_row
from vision_config import load_config

KINDS = ["straight", "hook", "uppercut"]
CLASSES = [("L", "straight"), ("R", "straight"), ("L", "hook"), ("R", "hook"),
           ("L", "uppercut"), ("R", "uppercut")]


def kind_of(punch):
    """Labels say jab/cross; the detector's 'kind' says straight. Compare on kind."""
    return "straight" if punch in ("jab", "cross", "straight") else punch


def class_name(hand, kind, stance):
    """Readable name, e.g. 'L jab' for an orthodox left straight."""
    if kind == "straight":
        lead = "L" if stance == "orthodox" else "R"
        return f"{hand} {'jab' if hand == lead else 'cross'}"
    return f"{hand} {'upper' if kind == 'uppercut' else kind}"


# ---------------------------------------------------------------------------------------
# Loading a session and running the detector over it
# ---------------------------------------------------------------------------------------

def load_session(session_dir, landmarks_name):
    meta = {}
    if os.path.exists(os.path.join(session_dir, "meta.json")):
        with open(os.path.join(session_dir, "meta.json")) as f:
            meta = json.load(f)

    path = os.path.join(session_dir, landmarks_name)
    if not os.path.exists(path):
        fallback = os.path.join(session_dir, "landmarks.csv")
        if landmarks_name != "landmarks.csv" and os.path.exists(fallback):
            print(f"  ({landmarks_name} not found in {session_dir}, using landmarks.csv)")
            path = fallback
        else:
            raise SystemExit(f"{path} not found - run extract_landmarks.py on this session first")

    # Frame size, needed to turn MediaPipe's 0-1 coordinates back into pixels
    width, height = meta.get("width", 640), meta.get("height", 480)
    info_path = path.replace(".csv", "_info.json")
    if os.path.exists(info_path):
        with open(info_path) as f:
            info = json.load(f)
        width, height = info.get("width", width), info.get("height", height)

    with open(os.path.join(session_dir, "labels.csv")) as f:
        labels = [dict(r, cue_time_s=float(r["cue_time_s"])) for r in csv.DictReader(f)]
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return meta, width, height, labels, rows, os.path.basename(path)


def run_detector(rows, width, height, config):
    """Feed every landmark row through the detector, exactly like the live system would."""
    detector = PunchDetector(config)
    events, last_t = [], 0.0
    for row in rows:
        t, pose = pose_from_csv_row(row, width, height)
        events += detector.update(t, pose)
        last_t = t
    events += detector.flush(last_t)
    return events


# ---------------------------------------------------------------------------------------
# Matching detections to cues
# ---------------------------------------------------------------------------------------

def match_events(labels, events, max_window):
    """
    Each cue owns a time window: from the cue until the next cue (at most max_window seconds).
    A detection belongs to the window its PEAK falls in. Per cue we pick the best candidate:
    the right hand if there is one, then the most confident. Everything else is an extra.
    Returns (matches, extras): matches[i] = event or None for cue i.
    """
    windows = []
    for i, cue in enumerate(labels):
        start = cue["cue_time_s"]
        end = labels[i + 1]["cue_time_s"] if i + 1 < len(labels) else start + max_window
        windows.append((start, min(end, start + max_window)))

    candidates = [[] for _ in labels]
    extras_outside = []
    for ev in events:
        for i, (start, end) in enumerate(windows):
            if start <= ev.t_peak < end:
                candidates[i].append(ev)
                break
        else:
            extras_outside.append(ev)

    matches, extras_inside = [], []
    for cue, cands in zip(labels, candidates):
        if not cands:
            matches.append(None)
            continue
        cands.sort(key=lambda e: (e.hand != cue["hand"], -e.conf))
        matches.append(cands[0])
        extras_inside += cands[1:]
    return matches, extras_inside, extras_outside


# ---------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------

def best_threshold(values_pos, values_neg):
    """
    Threshold t that best separates two groups (pos >= t, neg < t). Returns (t, accuracy).
    Used to SUGGEST settings - check them on a second session before trusting them.
    """
    values = sorted(set(values_pos + values_neg))
    if not values_pos or not values_neg:
        return None, None
    best = (None, -1.0)
    candidates = [(a + b) / 2 for a, b in zip(values, values[1:])] or values
    total = len(values_pos) + len(values_neg)
    for t in candidates:
        correct = sum(v >= t for v in values_pos) + sum(v < t for v in values_neg)
        if correct / total > best[1]:
            best = (t, correct / total)
    return best


def summarise(title, results, stance, config, minutes):
    """Print the scores for one session (or all sessions together)."""
    cues = len(results["labels"])
    detected = [(c, m) for c, m in zip(results["labels"], results["matches"]) if m is not None]
    right_hand = [(c, m) for c, m in detected if m.hand == c["hand"]]
    right_both = [(c, m) for c, m in right_hand if m.kind == kind_of(c["punch"])]
    extras = len(results["extras_inside"]) + len(results["extras_outside"])

    def pct(n, d):
        return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"

    print(f"\n=== {title} ===")
    print(f"  Detected         {pct(len(detected), cues)}   (missed {cues - len(detected)})")
    print(f"  Right hand       {pct(len(right_hand), len(detected))}   of detected")
    print(f"  Right hand+type  {pct(len(right_both), cues)}   of all punches thrown  <- headline number")
    per_min = f"{extras / minutes:.1f}/min" if minutes else "-"
    print(f"  Extra detections {extras}  ({per_min}; {len(results['extras_inside'])} near a cue, "
          f"{len(results['extras_outside'])} between cues)")
    if results["dropped_low_conf"]:
        print(f"  Dropped as low-confidence (< {config['classifier']['min_confidence_to_send']}): "
              f"{results['dropped_low_conf']}")

    # Confusion matrix: rows = what was thrown, columns = what vision said
    names = [class_name(h, k, stance) for h, k in CLASSES]
    col_w = max(len(n) for n in names) + 1
    print("\n  Confusion matrix (rows = thrown, columns = detected as)")
    print("  " + " " * col_w + "".join(f"{n:>{col_w}}" for n in names) + f"{'missed':>{col_w}}")
    for (h, k), name in zip(CLASSES, names):
        row_counts = [0] * (len(CLASSES) + 1)
        for c, m in zip(results["labels"], results["matches"]):
            if c["hand"] != h or kind_of(c["punch"]) != k:
                continue
            if m is None:
                row_counts[-1] += 1
            else:
                row_counts[CLASSES.index((m.hand, m.kind))] += 1
        if sum(row_counts):
            print(f"  {name:<{col_w}}" + "".join(f"{n:>{col_w}}" for n in row_counts))

    # Timing: cue -> peak is mostly the athlete's reaction time; frames per punch shows
    # how little the camera sees of each punch at this frame rate
    lags = sorted(m.t_peak - c["cue_time_s"] for c, m in detected)
    frames = sorted(m.features["frames"] for _, m in detected)
    if lags:
        print(f"\n  Cue -> peak: median {lags[len(lags) // 2]:.2f} s   "
              f"| frames per punch: median {frames[len(frames) // 2]}, min {frames[0]}, max {frames[-1]}")
        # How long after the peak the detector knew the punch was over (the hub gets it this much
        # later, plus the pose model's time per frame and a few ms of sending)
        waits = sorted((m.t_end - m.t_peak) * 1000 for _, m in detected)
        print(f"  Detection delay (peak -> punch recognised): median {waits[len(waits) // 2]:.0f} ms, "
              f"max {waits[-1]:.0f} ms   (+ ~1 pose-model frame before it reaches the hub)")

    # Suggested thresholds from this data
    use_3d = config["classifier"]["use_world_angles"]
    angle_key = "elbow_3d" if use_3d else "elbow_2d"
    straight = [m.features[angle_key] for c, m in right_hand
                if kind_of(c["punch"]) == "straight" and m.features[angle_key] is not None]
    bent = [m.features[angle_key] for c, m in right_hand
            if kind_of(c["punch"]) != "straight" and m.features[angle_key] is not None]
    t, acc = best_threshold(straight, bent)
    print("\n  Suggested thresholds for THIS data (check on another session before adopting):")
    if t is not None:
        print(f"    classifier.straight_min_elbow_deg = {t:.0f}   (now {config['classifier']['straight_min_elbow_deg']}; "
              f"separates straight vs bent {100 * acc:.0f}% of the time, using {angle_key})")
    ups = [m.features["uppercut_score"] for c, m in right_hand if kind_of(c["punch"]) == "uppercut"]
    hooks = [m.features["uppercut_score"] for c, m in right_hand if kind_of(c["punch"]) == "hook"]
    t, acc = best_threshold(ups, hooks)
    if t is not None:
        print(f"    classifier.uppercut_threshold   = {t:.2f}  (now {config['classifier']['uppercut_threshold']}; "
              f"separates uppercut vs hook {100 * acc:.0f}% of the time)")
    if not straight and not ups:
        print("    (not enough correctly-handed detections yet)")


def write_features(path, all_results):
    """One row per detection with its measurements and the truth, for tuning or training."""
    feature_names = []
    for res in all_results:
        for ev in res["events"]:
            feature_names = list(ev.features.keys())
            break
        if feature_names:
            break
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["session", "cue_index", "true_hand", "true_kind", "pred_hand", "pred_kind",
                    "correct", "conf", "cue_time_s", "t_peak"] + feature_names)
        for res in all_results:
            matched = {id(m): i for i, m in enumerate(res["matches"]) if m is not None}
            for ev in res["events"]:
                i = matched.get(id(ev))
                cue = res["labels"][i] if i is not None else None
                true_hand = cue["hand"] if cue else ""
                true_kind = kind_of(cue["punch"]) if cue else "none"
                correct = int(cue is not None and ev.hand == true_hand and ev.kind == true_kind)
                w.writerow([res["name"], i if i is not None else "", true_hand, true_kind, ev.hand, ev.kind,
                            correct, f"{ev.conf:.3f}", f"{cue['cue_time_s']:.3f}" if cue else "",
                            f"{ev.t_peak:.3f}"] + [ev.features.get(k) for k in feature_names])


def save_plot(path, combined, stance, config):
    """Confusion matrix + scatter of the two measurements the rules use."""
    import matplotlib
    matplotlib.use("Agg")              # draw to a file, no window needed (works over SSH)
    import matplotlib.pyplot as plt

    names = [class_name(h, k, stance) for h, k in CLASSES]
    matrix = [[0] * (len(CLASSES) + 1) for _ in CLASSES]
    for c, m in zip(combined["labels"], combined["matches"]):
        r = CLASSES.index((c["hand"], kind_of(c["punch"])))
        matrix[r][-1 if m is None else CLASSES.index((m.hand, m.kind))] += 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.5))
    ax1.imshow(matrix, cmap="Blues")
    ax1.set_xticks(range(len(names) + 1), names + ["missed"], rotation=40, ha="right")
    ax1.set_yticks(range(len(names)), names)
    ax1.set_xlabel("detected as")
    ax1.set_ylabel("thrown")
    ax1.set_title("Confusion matrix")
    for r, row in enumerate(matrix):
        for c, n in enumerate(row):
            if n:
                ax1.text(c, r, n, ha="center", va="center", color="black")

    use_3d = config["classifier"]["use_world_angles"]
    angle_key = "elbow_3d" if use_3d else "elbow_2d"
    colours = {"straight": "tab:blue", "hook": "tab:orange", "uppercut": "tab:green"}
    for c, m in zip(combined["labels"], combined["matches"]):
        if m is None or m.features.get(angle_key) is None:
            continue
        k = kind_of(c["punch"])
        ax2.scatter(m.features[angle_key], m.features["uppercut_score"], color=colours[k],
                    marker="o" if c["hand"] == "L" else "^", alpha=0.75)
    for k, col in colours.items():
        ax2.scatter([], [], color=col, label=f"thrown: {k}")
    ax2.scatter([], [], color="grey", marker="o", label="left hand")
    ax2.scatter([], [], color="grey", marker="^", label="right hand")
    ax2.axvline(config["classifier"]["straight_min_elbow_deg"], ls="--", color="grey")
    ax2.axhline(config["classifier"]["uppercut_threshold"], ls=":", color="grey")
    ax2.set_xlabel(f"elbow angle at peak ({'3D' if use_3d else '2D'}, degrees)  — right of dashed line = straight")
    ax2.set_ylabel("uppercut score — above dotted line = uppercut")
    ax2.set_title("What the rules see (each dot = one detected punch)")
    ax2.legend(fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    print(f"\nSaved chart to {path}")


def main():
    parser = argparse.ArgumentParser(description="Score the punch detector against labelled sessions")
    parser.add_argument("sessions", nargs="+", help="session folders, e.g. recordings/test_left")
    parser.add_argument("--landmarks", default="landmarks_9fps.csv",
                        help="which landmark file to use in each session (default: simulated Pi speed)")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--set", action="append", default=[], metavar="SECTION.NAME=VALUE",
                        help="try a setting without editing config.json (can repeat)")
    parser.add_argument("--window", type=float, default=2.0,
                        help="max seconds after a cue in which its punch must peak")
    parser.add_argument("--all", action="store_true",
                        help="keep low-confidence detections (default: drop them, like the live sender)")
    parser.add_argument("--plot", action="store_true", help="save evaluation.png")
    parser.add_argument("--out", help="folder for features.csv / evaluation.png (default: first session)")
    args = parser.parse_args()

    config = load_config(args.config, args.set)
    min_conf = 0.0 if args.all else config["classifier"]["min_confidence_to_send"]

    all_results = []
    combined = {"labels": [], "matches": [], "extras_inside": [], "extras_outside": [], "dropped_low_conf": 0}
    total_minutes = 0.0
    stance = config["player"]["stance"]

    for session in args.sessions:
        session = session.rstrip("/\\")
        meta, width, height, labels, rows, used = load_session(session, args.landmarks)
        stance = meta.get("stance", config["player"]["stance"])
        session_config = load_config(args.config, args.set + [f"player.stance={stance}"])

        events = run_detector(rows, width, height, session_config)
        kept = [e for e in events if e.conf >= min_conf]
        matches, extras_in, extras_out = match_events(labels, kept, args.window)
        minutes = (float(rows[-1]["time_s"]) - float(rows[0]["time_s"])) / 60 if rows else 0

        result = {"name": os.path.basename(session), "labels": labels, "matches": matches,
                  "extras_inside": extras_in, "extras_outside": extras_out, "events": kept,
                  "dropped_low_conf": len(events) - len(kept)}
        all_results.append(result)
        summarise(f"{session}  ({meta.get('angle', '?')}, {stance}, {len(labels)} punches, {used}, {width}x{height})",
                  result, stance, session_config, minutes)

        for key in ("labels", "matches", "extras_inside", "extras_outside"):
            combined[key] += result[key]
        combined["dropped_low_conf"] += result["dropped_low_conf"]
        total_minutes += minutes

    if len(all_results) > 1:
        summarise(f"ALL {len(all_results)} SESSIONS", combined, stance, config, total_minutes)

    out_dir = args.out or args.sessions[0].rstrip("/\\")
    os.makedirs(out_dir, exist_ok=True)
    write_features(os.path.join(out_dir, "features.csv"), all_results)
    print(f"\nSaved per-punch measurements to {os.path.join(out_dir, 'features.csv')}")
    if args.plot:
        save_plot(os.path.join(out_dir, "evaluation.png"), combined, stance, config)


if __name__ == "__main__":
    main()
