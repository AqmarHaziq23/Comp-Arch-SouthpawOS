# Computer vision: guard tracking (Member D, Johanan)

A webcam on a tripod watches the boxer and checks, about 9 times a second, whether each
hand is **up in guard**. The hub turns that into each round's **guard %** and **number of
drops**, leaving out the moments when the wrist boards saw a punch (a punching hand leaves
the guard on purpose).

Runs on the Pi as its own process. The game keeps working when it's off.

```
webcam ─► camera.py ─► MediaPipe Pose ─► guard.py ──────────► sender.py ─► hub (UDP 127.0.0.1:5005)
          newest frame   33 body points    hand up / down /      {"kind":"guard","L":true,"R":false}
          only, reconnects                 can't tell             + heartbeat every second
```

## The guard rule

For each frame and each hand (all numbers in `config.json` → `guard`):

- The **fist** (wrist averaged with the knuckle points) must be no lower than about the bottom of the chin:
  at most `rear_max_below_mouth` / `lead_max_below_mouth` (both 0.25) below the mouth.
  Lower `lead_max_below_mouth` to demand a higher lead hand.
- and no further than `max_side_reach` (0.8) sideways from the nose.

These were tuned on a first 45 s session. In guard, the fists sat 0–0.15 below the mouth.
Hands lowered to the collarbone were about 0.3–0.4 below, and dropped hands 0.4 or more.
- A hand counts as a **drop** only after staying down for `drop_hold_s` (0.4 s), so quick dips don't count.
- If the hand or face can't be seen, the answer is **"can't tell"**. That time doesn't count either way.

Distances are in **body lengths** (shoulders to hips as the camera sees it, ~0.5 m), so the same
numbers work whether the boxer stands near or far. Lead = left hand for `orthodox`, right for `southpaw`
(`player.stance`).

## Setup

The Pi 4 needs **MediaPipe 0.10.18** (newer versions need CPU instructions the Pi 4 lacks and crash with
"Illegal instruction"). That version needs **Python 3.12 or older**, and the Pi has 3.13, so use uv:

```bash
# Pi
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.local/bin/env
cd ~/Comp-Arch-SouthpawOS/vision
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt
chmod +x run_vision.sh
```

```powershell
# Windows with Python 3.9-3.12 installed
cd vision
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The pose model `pose_landmarker_lite.task` is in the repo.

## Run

```bash
./run_vision.sh                       # Pi, no window (over SSH / demo day)
./run_vision.sh --window              # Pi desktop, with the overlay window
python vision_main.py                 # any machine, webcam 0, with a window
python vision_main.py --source 1      # webcam 1 (e.g. the C922 on a laptop with a built-in camera)
```

**The window shows:** skeleton, each hand's limit line (L / R), fists circled **green** (up), **red** (down) or
grey (can't tell), live guard % and drops, and a big **HANDS UP!** while a hand is dropped.
Press **Q** to quit. On exit it prints the session's guard % and drops (punches included, since
without the hub it can't tell punches apart).

**Test without the hub:** `python udp_listener.py` in a second terminal prints what vision sends.

### Tuning on recorded clips

```bash
python vision_main.py --source 1 --record-raw recordings/guard1      # box, then Q: saves the plain camera video
python vision_main.py --source recordings/guard1/video.avi           # replay it in real time
python vision_main.py --source recordings/guard1/video.avi --set guard.rear_max_below_mouth=0.2   # try a setting
python vision_main.py --source recordings/guard1/video.avi --fast --no-display --record overlay.mp4   # overlay video
```

`recordings/` and videos are git-ignored (too big for GitHub).

### Tests (no camera or MediaPipe needed)

```bash
python test_guard.py        # ALL TESTS PASSED
```

## Messages to the hub

One JSON object per UDP datagram to `127.0.0.1:5005`:

```json
{"v":1, "src":"vision", "kind":"guard",  "t":1731.42, "age_ms":120, "L":true, "R":false}
{"v":1, "src":"vision", "kind":"status", "t":1732.0, "fps":8.9, "person":true, "camera":true}
```

- `guard` is sent for every processed frame (~9 a second). `L`/`R` = `true` (up), `false` (down) or
  `null` (can't tell). `L`/`R` are the **player's** left and right, like `wristL`/`wristR`.
- `t` = frame capture time, `time.monotonic()`: the same clock as the hub on the same Pi.

## Files

| File | Purpose |
|---|---|
| `vision_main.py` | **Run this.** Camera → pose → guard check → hub, overlay, recording |
| `guard.py` | The guard rule (`GuardChecker`) and guard % / drop counting (`GuardTracker`) |
| `pose.py` | MediaPipe result → named body points |
| `camera.py` | Webcam (newest frame only, auto-reconnect) or a video clip that behaves like one |
| `sender.py` | UDP messages to the hub |
| `vision_config.py` | Every setting's default; `config.json` overrides them |
| `config.json` | The settings to tune (each section has a `_comment`) |
| `udp_listener.py` | Fake hub for testing |
| `test_guard.py` | Tests for the guard rule |
| `run_vision.sh` | One-command start on the Pi (slightly lower CPU priority than the hub) |

## Credits

- **MediaPipe Pose Landmarker** (BlazePose GHUM 3D, lite model), Google, Apache 2.0:
  https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker.
  V. Bazarevsky et al., "BlazePose: On-device Real-time Body Pose tracking", 2020.
- **OpenCV** (Apache 2.0): https://opencv.org
