"""
tools/synth_session.py - Southpaw OS, computer vision (Member D)

Makes a FAKE labelled session (no camera needed) for testing the vision code end to end:
a simple 3D boxer throws the cued punches, a virtual camera films it, and the result is
written in the same format as record_clips.py + extract_landmarks.py produce:

    <out>/<session>/labels.csv, frames.csv, meta.json,
                    landmarks.csv (every frame) and landmarks_9fps.csv (frames the Pi would see)

It is NOT a substitute for real recordings (real people, blur and MediaPipe errors are messier),
but it proves the pipeline works and gives a baseline. Useful for teammates too: Member C can
replay a synthetic session through vision_main.py to test the hub without a camera.

Examples (from the vision folder):
  python tools/synth_session.py --session synth_45L
  python tools/synth_session.py --session synth_high --cam-height 0.6 --reps 10 --shuffle
"""

import argparse
import csv
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # import from vision/
from extract_landmarks import csv_header, pick_frames_like_the_pi, POINTS   # noqa: E402
from record_clips import build_plan                                          # noqa: E402

UPPER_ARM, FOREARM, FIST = 0.29, 0.27, 0.08     # metres

# Typical boxing combos (orthodox): 1 = jab, 2 = cross, 3 = lead hook, 5/6 = uppercuts
COMBOS = [
    ["L jab", "R cross"], ["L jab", "R cross", "L hook"], ["L jab", "L jab", "R cross"],
    ["R cross", "L hook", "R cross"], ["L jab", "R uppercut"], ["L hook", "R cross"],
    ["L uppercut", "R uppercut"], ["L jab", "R cross", "L uppercut", "R cross"], ["L hook", "R hook"],
]


# --- tiny 3D vector helpers ------------------------------------------------------------------
def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
def dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def norm(a): return math.sqrt(dot(a, a))
def unit(a): n = norm(a) or 1.0; return mul(a, 1.0 / n)
def cross(a, b): return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
def lerp(a, b, u): return tuple(x + (y - x) * u for x, y in zip(a, b))
def smooth(u): u = max(0.0, min(1.0, u)); return u * u * (3 - 2 * u)


def yaw(p, deg):
    """Rotate a point about the vertical axis (body frame: x = athlete's right, y = down, z = forward)."""
    r = math.radians(deg)
    return (p[0] * math.cos(r) + p[2] * math.sin(r), p[1], -p[0] * math.sin(r) + p[2] * math.cos(r))


def solve_elbow(shoulder, wrist, pole):
    """Two-bone arm: where the elbow goes for a given shoulder and wrist ('pole' = which way it bends)."""
    d_vec = sub(wrist, shoulder)
    d = max(abs(UPPER_ARM - FOREARM) + 1e-3, min(UPPER_ARM + FOREARM - 1e-3, norm(d_vec)))
    direction = unit(d_vec)
    wrist = add(shoulder, mul(direction, d))
    cos_a = (UPPER_ARM ** 2 + d ** 2 - FOREARM ** 2) / (2 * UPPER_ARM * d)
    a = math.acos(max(-1.0, min(1.0, cos_a)))
    perp = unit(sub(pole, mul(direction, dot(pole, direction))))
    elbow = add(shoulder, add(mul(direction, UPPER_ARM * math.cos(a)), mul(perp, UPPER_ARM * math.sin(a))))
    return elbow, wrist


# --- the boxer -----------------------------------------------------------------------------
# Wrist targets are offsets from the (rotated) shoulder, in the body frame. Orthodox stance.
GUARD = {"L": ((0.06, -0.10, 0.22), (-0.4, 1, 0)), "R": ((-0.07, -0.12, 0.17), (0.4, 1, 0))}
# Key poses per punch: list of (seconds from punch start, wrist offset, elbow pole, torso yaw deg)
def keyframes(hand, kind):
    s = -1 if hand == "L" else 1                     # mirror left/right
    g_off, g_pole = GUARD[hand]
    twist = -8 if hand == "L" else 22                # rear-hand punches turn the torso more
    if kind == "straight":
        # Nearly full extension (elbow ~160 deg), slightly across the body for the rear hand
        end = ((-0.13 if hand == "R" else 0.03), 0.02, 0.535)
        return [(0.0, g_off, g_pole, 0), (0.17, end, (0, 1, 0), twist),
                (0.23, end, (0, 1, 0), twist), (0.50, g_off, g_pole, 0)]
    if kind == "hook":
        mid = (0.10 * s, 0.0, 0.26)                  # swing out to the side first
        end = (-0.21 * s, 0.03, 0.29)                # then across, at shoulder height
        pole = (s * 1.0, -0.05, 0.1)                 # elbow out to the side, raised
        return [(0.0, g_off, g_pole, 0), (0.10, mid, pole, twist * 0.5), (0.20, end, pole, twist),
                (0.25, end, pole, twist), (0.52, g_off, g_pole, 0)]
    if kind == "uppercut":
        dip = (-0.02 * s, 0.03, 0.20)                # drop the hand ~13 cm first
        end = (-0.07 * s, -0.05, 0.40)               # drive up and forward to chin height (~25 cm out)
        pole = (0.15 * s, 1, 0.25)                   # elbow down, close to the ribs
        return [(0.0, g_off, g_pole, 0), (0.10, dip, (0.2 * s, 1, 0), twist * 0.3),
                (0.24, end, pole, twist), (0.29, end, pole, twist), (0.56, g_off, g_pole, 0)]
    raise ValueError(kind)


def sample(keys, t):
    """Interpolate the key poses at time t (seconds since punch start)."""
    if t <= keys[0][0]:
        return keys[0][1:]
    for (t0, o0, p0, y0), (t1, o1, p1, y1) in zip(keys, keys[1:]):
        if t <= t1:
            u = smooth((t - t0) / (t1 - t0))
            return lerp(o0, o1, u), lerp(p0, p1, u), y0 + (y1 - y0) * u
    return keys[-1][1:]


def body_points(t, punches, rng_sway):
    """All body points (body frame, metres) at time t, given the scheduled punches."""
    bob = 0.015 * math.sin(2 * math.pi * 0.8 * t)                 # light bounce
    base = {
        "L_HIP": (-0.10, 0.0, 0.03), "R_HIP": (0.10, 0.0, -0.03),
        "L_SHOULDER": (-0.17, -0.48, 0.06), "R_SHOULDER": (0.17, -0.48, -0.06),
        "NOSE": (0.0, -0.68, 0.08),
    }
    pts = {}
    torso_yaw = 0.0
    arm_pose = {}
    for hand in ("L", "R"):
        g_off, g_pole = GUARD[hand]
        sway = (0.012 * math.sin(2 * math.pi * (0.5 + 0.1 * (hand == "R")) * t + rng_sway[hand]), 0.01 * math.sin(1.3 * t), 0.0)
        arm_pose[hand] = (add(g_off, sway), g_pole, 0.0)
        for p in punches:
            if p["hand"] == hand and p["t0"] <= t <= p["t0"] + 0.6:
                arm_pose[hand] = sample(p["keys"], t - p["t0"])
                torso_yaw = arm_pose[hand][2]
    for name, p in base.items():
        q = (p[0], p[1] + (bob if "HIP" not in name else bob * 0.3), p[2])
        pts[name] = yaw(q, torso_yaw) if "HIP" not in name else q
    for hand in ("L", "R"):
        off, pole, _ = arm_pose[hand]
        shoulder = pts[f"{hand}_SHOULDER"]
        elbow, wrist = solve_elbow(shoulder, add(shoulder, yaw(off, torso_yaw)), yaw(pole, torso_yaw))
        fist = add(wrist, mul(unit(sub(wrist, elbow)), FIST))
        pts[f"{hand}_ELBOW"], pts[f"{hand}_WRIST"], pts[f"{hand}_INDEX"] = elbow, wrist, fist
        pts[f"{hand}_PINKY"] = add(fist, (0.0, 0.02, 0.0))
        pts[f"{hand}_THUMB"] = add(fist, (0.0, -0.02, -0.01))
    return pts


class Camera:
    """Pinhole camera at angle_deg to the athlete's front (negative = athlete's left side)."""

    def __init__(self, angle_deg, distance, height_above_shoulder, width=640, height=480, focal=560):
        a = math.radians(angle_deg)
        self.pos = (distance * math.sin(a), -0.48 - height_above_shoulder, distance * math.cos(a) + 0.3)
        target = (0.0, -0.35, 0.25)
        self.f = unit(sub(target, self.pos))
        self.r = unit(cross((0, 1, 0), self.f))
        self.d = cross(self.f, self.r)
        self.w, self.h, self.focal = width, height, focal

    def to_camera(self, p):
        rel = sub(p, self.pos)
        return dot(rel, self.r), dot(rel, self.d), dot(rel, self.f)

    def project(self, p):
        x, y, z = self.to_camera(p)
        return (self.w / 2 + self.focal * x / z) / self.w, (self.h / 2 + self.focal * y / z) / self.h, z

    def world_axes(self, p, hips_mid):
        """MediaPipe-style world landmark: metres from the hip midpoint, in camera-aligned axes."""
        rel = sub(p, hips_mid)
        return dot(rel, self.r), dot(rel, self.d), dot(rel, self.f)


def main():
    ap = argparse.ArgumentParser(description="Make a synthetic labelled punch session")
    ap.add_argument("--session", default="synth_45L")
    ap.add_argument("--out", default="recordings")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--interval", type=float, default=2.5)
    ap.add_argument("--shuffle", action="store_true")
    ap.add_argument("--angle", type=float, default=-45, help="camera angle from the front (neg = athlete's left)")
    ap.add_argument("--distance", type=float, default=2.2)
    ap.add_argument("--cam-height", type=float, default=0.0, help="metres above shoulder height")
    ap.add_argument("--pixel-noise", type=float, default=1.5)
    ap.add_argument("--world-noise", type=float, default=0.015)
    ap.add_argument("--fidgets", type=int, default=6, help="small guard adjustments (should NOT be detected)")
    ap.add_argument("--combos", type=int, default=0, help="make N quick combos instead of single cued punches")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    if args.combos:
        # Combo mode: groups of punches ~0.4 s apart, like the real game. Each label's cue time is
        # the moment that punch STARTS, so evaluate with a short window:  evaluate.py ... --window 0.6
        plan, t = [], 5.0
        for k in range(args.combos):
            for name in rng.choice(COMBOS):
                hand, punch = name.split()
                plan.append({"hand": hand, "punch": punch, "rep": k + 1, "planned_t": t})
                t += rng.uniform(0.35, 0.45)
            t += 2.5
        end_time = t + 1.0
    else:
        plan, end_time = build_plan(args.reps, args.shuffle, 5, args.interval, 4.0)
    punches = []
    for cue in plan:
        kind = "straight" if cue["punch"] in ("jab", "cross") else cue["punch"]
        reaction = 0.03 if args.combos else max(0.15, rng.gauss(0.35, 0.08))
        t0 = cue["planned_t"] + reaction
        punches.append({"hand": cue["hand"], "t0": t0, "keys": keyframes(cue["hand"], kind)})
    # Small guard adjustments between punches: the detector should ignore these
    for _ in range(args.fidgets):
        hand = rng.choice("LR")
        t0 = rng.uniform(6, end_time - 2)
        g_off, g_pole = GUARD[hand]
        nudge = add(g_off, (rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), 0.04))
        punches.append({"hand": hand, "t0": t0,
                        "keys": [(0.0, g_off, g_pole, 0), (0.15, nudge, g_pole, 0), (0.45, g_off, g_pole, 0)]})

    cam = Camera(args.angle, args.distance, args.cam_height)
    session_dir = os.path.join(args.out, args.session)
    os.makedirs(session_dir, exist_ok=True)

    # 30 fps capture times with a little jitter, like a real webcam
    times, t = [], 0.0
    while t < end_time:
        times.append(t)
        t += 1 / 30 + rng.gauss(0, 0.002)
    rng_sway = {"L": rng.uniform(0, 6), "R": rng.uniform(0, 6)}

    rows = []
    for i, t in enumerate(times):
        pts = body_points(t, punches, rng_sway)
        hips_mid = lerp(pts["L_HIP"], pts["R_HIP"], 0.5)
        # Crude occlusion: the far (right) arm is hidden when it's behind the chest from the camera's view
        chest = lerp(pts["L_SHOULDER"], pts["R_SHOULDER"], 0.5)
        _, _, chest_z = cam.to_camera(chest)
        row = [i, f"{t:.4f}", 1]
        for name in POINTS:
            p = pts[name]
            x, y, z = cam.project(p)
            vis = 0.97
            if name.startswith("R_") and name not in ("R_SHOULDER", "R_HIP"):
                px, _, pz = cam.project(p)
                cx, _, _ = cam.project(chest)
                if pz > chest_z + 0.05 and abs(px - cx) < 0.06:
                    vis = 0.35
            x += rng.gauss(0, args.pixel_noise) / cam.w
            y += rng.gauss(0, args.pixel_noise) / cam.h
            wx, wy, wz = (v + rng.gauss(0, args.world_noise) for v in cam.world_axes(p, hips_mid))
            row += [f"{x:.5f}", f"{y:.5f}", f"{z:.5f}", f"{vis:.3f}", f"{wx:.5f}", f"{wy:.5f}", f"{wz:.5f}"]
        rows.append(row)

    with open(os.path.join(session_dir, "frames.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "time_s"])
        w.writerows([i, f"{t:.4f}"] for i, t in enumerate(times))
    with open(os.path.join(session_dir, "labels.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cue_index", "hand", "punch", "rep", "cue_frame", "cue_time_s"])
        for i, cue in enumerate(plan):
            frame = next(j for j, t in enumerate(times) if t >= cue["planned_t"])
            w.writerow([i, cue["hand"], cue["punch"], cue["rep"], frame, f"{times[frame]:.4f}"])
    for name, keep in (("landmarks.csv", None), ("landmarks_9fps.csv", pick_frames_like_the_pi(times, 9))):
        with open(os.path.join(session_dir, name), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(csv_header())
            w.writerows(r for r in rows if keep is None or r[0] in keep)
        with open(os.path.join(session_dir, name.replace(".csv", "_info.json")), "w") as f:
            json.dump({"width": cam.w, "height": cam.h, "synthetic": True}, f)
    with open(os.path.join(session_dir, "meta.json"), "w") as f:
        json.dump({"session": args.session, "angle": f"synthetic {args.angle:+.0f} deg, "
                   f"{args.cam_height:+.1f} m vs shoulders", "stance": "orthodox", "width": cam.w,
                   "height": cam.h, "synthetic": True, "reps": args.reps, "interval_s": args.interval}, f, indent=2)
    print(f"Wrote synthetic session {session_dir}: {len(plan)} punches, {len(times)} frames, {end_time:.0f} s")


if __name__ == "__main__":
    main()
