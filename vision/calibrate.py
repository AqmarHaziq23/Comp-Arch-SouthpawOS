"""
calibrate.py - Southpaw OS, computer vision (Member D)

Marks where the bag is in the camera image (the "bag zone") for contact detection.
Run it once per camera setup: at home, at school, and again at the demo venue,
because the zone is only valid while the camera doesn't move.

With a desktop (Raspberry Pi Connect):
    python calibrate.py
  Click around the bag's HITTING AREA (at least 3 points; 4 corners is usual), then:
    S = save    U = undo last point    R = start again    Q = quit without saving

Without a desktop (over SSH):
    python calibrate.py --snapshot snap.jpg      saves one camera frame; open it on your laptop,
                                                 read off the corner pixel positions, then:
    python calibrate.py --points "210,120 330,118 335,400 205,405"

Saves calibration.local.json (git-ignored: every camera position is different).
"""

import argparse
import datetime
import json

import cv2

from vision_config import load_config


def grab_frame(source, width, height):
    """One frame from the webcam (number or /dev path) or a video file."""
    if str(source).isdigit() or str(source).startswith("/dev/"):
        cap = cv2.VideoCapture(int(source) if str(source).isdigit() else source, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        for _ in range(10):          # let auto-exposure settle for a few frames
            cap.read()
    else:
        cap = cv2.VideoCapture(source)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Could not read a frame from {source}")
    return frame


def save(points, frame, path, source):
    h, w = frame.shape[:2]
    data = {"bag_zone": [[int(x), int(y)] for x, y in points], "width": w, "height": h,
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "source": str(source)}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {len(points)} bag-zone points to {path}")


def main():
    parser = argparse.ArgumentParser(description="Mark the bag zone for contact detection")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--source", help="webcam number/path or a video file (default: camera.source)")
    parser.add_argument("--points", help='corners without a window, e.g. "210,120 330,118 335,400 205,405"')
    parser.add_argument("--snapshot", help="just save one frame to this image file and stop")
    args = parser.parse_args()

    config = load_config(args.config)
    source = args.source or config["camera"]["source"]
    out_path = config["contact"]["calibration_file"]
    frame = grab_frame(source, config["camera"]["width"], config["camera"]["height"])

    if args.snapshot:
        cv2.imwrite(args.snapshot, frame)
        print(f"Saved {args.snapshot} ({frame.shape[1]}x{frame.shape[0]}). Read the bag corners off it, "
              f'then run: python calibrate.py --points "x1,y1 x2,y2 x3,y3 x4,y4"')
        return

    if args.points:
        points = [tuple(int(v) for v in p.split(",")) for p in args.points.split()]
        if len(points) < 3:
            raise SystemExit("Need at least 3 points")
        save(points, frame, out_path, source)
        return

    # Interactive: click the corners on the live frame
    points = []
    window = "Click the bag's hitting area corners - S save, U undo, R reset, Q quit"

    def on_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_click)
    while True:
        view = frame.copy()
        for i, p in enumerate(points):
            cv2.circle(view, p, 5, (0, 200, 255), -1)
            if i:
                cv2.line(view, points[i - 1], p, (0, 200, 255), 2)
        if len(points) >= 3:
            cv2.line(view, points[-1], points[0], (0, 200, 255), 1)
        cv2.putText(view, f"{len(points)} points   S=save U=undo R=reset Q=quit", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.imshow(window, view)
        key = cv2.waitKey(30) & 0xFF
        if key == ord("u") and points:
            points.pop()
        elif key == ord("r"):
            points.clear()
        elif key == ord("s"):
            if len(points) >= 3:
                save(points, frame, out_path, source)
                break
            print("Need at least 3 points before saving")
        elif key == ord("q"):
            print("Quit without saving")
            break
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
