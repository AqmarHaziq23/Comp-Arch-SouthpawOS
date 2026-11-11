# Computer vision — Member D (Johanan)

MediaPipe pose tracking on the Pi. Runs as its own process and sends one JSON
object per UDP datagram to `127.0.0.1:5005` (format in `protocol/PROTOCOL.md`).
The game must keep working when this process is off.

## What it does

The wrist boards are the main punch detector. Vision is a **second opinion**,
focused on what the accelerometers are worst at:

1. **Hook vs uppercut** — the accelerometer's most common mix-up.
2. **Contact** — did the fist actually reach the bag?

```
webcam ─► camera.py ─► pose model (33 body points) ─► punch_detector.py ─► contact.py ─► sender.py ─► hub (Member C)
                                                            │
                                                            └─► overlay window / recording (vision_main.py)
```

## Status

| Part | Status |
|---|---|
| Pi + webcam setup | ✅ Done |
| `pose_preview.py` — live skeleton, wrist trails, FPS | ✅ Working (~9 fps on the Pi 4) |
| Labelled clips (`record_clips.py`) + landmark extraction | ✅ Working; first test clips recorded (45° left tracked best) |
| Punch detector + classifier (`punch_detector.py`) | ✅ Built; 93–100% on synthetic data — **needs tuning on real clips** |
| Evaluation (`evaluate.py`, `plot_punches.py`) | ✅ Built |
| Bag-zone calibration + contact (`calibrate.py`, `contact.py`) | ✅ Built; needs a real bag to calibrate |
| Live process → hub (`vision_main.py`, `sender.py`) | ✅ Built; tested end to end with a fake hub — **needs testing with C's hub** |

## Hardware

- **Raspberry Pi 4 Model B**, 64-bit Raspberry Pi OS (Trixie), hostname `southpaw`, with fan
- **Logitech C922** USB webcam on `/dev/video0` (blue USB 3 port), 640×480 @ 30 fps MJPG
- **Official 5V 3A power supply** — anything weaker causes under-voltage and throttling
  (check with `vcgencmd get_throttled`; it must say `throttled=0x0`)
- Tripod, and a lamp for even lighting (reduces motion blur on fast punches)

## Setup

### ⚠️ Why MediaPipe 0.10.18 and Python 3.12

**MediaPipe 1.x crashes on a Raspberry Pi 4** with
`This binary was compiled with aes enabled, but this feature is not available on this processor`.
It needs the CPU's AES crypto instructions, which the Pi 4's Cortex-A72 doesn't have.
**0.10.18** is the last version without that requirement, and it only supports Python ≤ 3.12.
Pi OS Trixie ships Python 3.13, so we use [uv](https://docs.astral.sh/uv/) to get a separate
Python 3.12 just for this project. Use the same versions on every machine so results match.

### On the Pi

```bash
# 1. Install uv (Python + package manager), then load it into this terminal
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env

# 2. Create the venv with Python 3.12 and install the pinned packages
cd ~/Comp-Arch-SouthpawOS/vision
uv venv --python 3.12 .venv
source .venv/bin/activate              # run this in every new terminal
uv pip install -r requirements.txt

# 3. Check
python -c "import mediapipe as mp; print(mp.__version__)"   # 0.10.18
```

Don't install `opencv-python` separately: MediaPipe already brings
`opencv-contrib-python`, and two OpenCV packages in one venv conflict.

### On a Windows laptop (for tuning, or as the backup vision machine)

```powershell
cd Comp-Arch-SouthpawOS\vision
uv venv --python 3.12 .venv
.venv\Scripts\activate
uv pip install -r requirements.txt
```

### Pose model

`pose_landmarker_lite.task` is committed in this folder. If it's missing:

```bash
wget -O pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
```

## Workflow

### 1. Record labelled clips (on the Pi desktop)

```bash
python record_clips.py --session 45L_run1 --angle 45-left --athlete Johanan --notes "lighting, distance..."
python record_clips.py --session 45L_mixed1 --angle 45-left --shuffle
```

### 2. Extract body points at the Pi's real speed (Pi or laptop)

```bash
python extract_landmarks.py recordings/45L_run1 recordings/45L_mixed1 --sim-fps 9
```

### 3. Look, score, tune (laptop is fastest)

```bash
python plot_punches.py recordings/45L_run1                          # see how the punch types differ
python evaluate.py recordings/45L_run1 recordings/45L_mixed1 --plot  # accuracy + suggested thresholds
python evaluate.py recordings/45L_run1 --set classifier.straight_min_elbow_deg=115   # try a value
```

Copy good values into `config.json`. Tune on one session, check on another.

### 4. Calibrate the bag zone (each time the camera moves)

```bash
python calibrate.py                    # click the bag's corners (needs the desktop)
python calibrate.py --snapshot snap.jpg && python calibrate.py --points "x1,y1 x2,y2 x3,y3 x4,y4"   # over SSH
```

### 5. Run live

```bash
python udp_listener.py                 # terminal 1: fake hub (skip when C's hub is running)
./run_vision.sh --window               # terminal 2: vision with overlay (Pi desktop)
./run_vision.sh                        # no window (SSH / demo day)
./run_vision.sh --record overlay.mp4   # save overlay footage for the YouTube video
```

## Messages to the hub

One JSON object per UDP datagram to `output.hub_host:output.hub_port` (default `127.0.0.1:5005`):

```json
{"v":1,"src":"vision","kind":"punch","t":1731.42,"t_wall":1794356123.5,"t_start":1731.21,"age_ms":142,
 "hand":"L","type":"hook","contact":true,"conf":0.82}
{"v":1,"src":"vision","kind":"status","t":1732.0,"fps":8.9,"person":true,"camera":true}
```

- `t` = capture time of the punch's **peak** frame, `time.monotonic()` on the Pi (same clock as the hub on the same Pi).
  `t_wall` = the same moment as `time.time()`, for a hub that uses the wall clock.
  If vision runs on another machine, use *receive time − `age_ms`*.
- `contact` is `null` when the bag zone isn't calibrated.
- Vision events arrive **~0.25–0.45 s after the punch peak** (detection waits for the punch to finish,
  plus one pose-model frame). The hub should allow for that when matching.
- `status` arrives once a second; no status for ~3 s = vision is off.

## Measured performance (Pi 4, lite model, 640×480)

| Measure | Value |
|---|---|
| Processing speed | ~8.9 fps |
| Pose model time | ~105 ms per frame |
| Frame age (capture → result) | ~120–140 ms |
| Punch peak → event at hub (simulated) | ~250–450 ms (median ~340) |

## Files

| File | Purpose |
|---|---|
| `vision_main.py` | **The live process**: camera → pose → punches → hub, overlay, heartbeat |
| `run_vision.sh` | One-command start (lower CPU priority than the hub) |
| `punch_detector.py` | Punch detection + classification (used live and by `evaluate.py`) |
| `vision_config.py` | Default settings + loading `config.json` and `--set` overrides |
| `config.json` | Thresholds, camera, hub address — tune here, not in code |
| `camera.py` | Webcam (newest-frame thread, auto-reconnect) and clip playback |
| `contact.py` / `calibrate.py` | Bag-zone contact check / marking the bag zone |
| `sender.py` | UDP JSON messages to the hub |
| `udp_listener.py` | Fake hub for testing; includes an asyncio receive example for Member C |
| `pose_preview.py` | Live pose preview + FPS test |
| `record_clips.py` | Records labelled punch clips |
| `extract_landmarks.py` | Clip → body-point CSV (optionally at simulated Pi speed) |
| `evaluate.py` | Scores the detector against labels; suggests thresholds |
| `plot_punches.py` | Charts each labelled punch's path and elbow angle |
| `tools/synth_session.py` | Fake labelled sessions for testing without a camera |
| `requirements.txt` | Pinned packages (`mediapipe==0.10.18`) |
| `pose_landmarker_lite.task` | The pose model |

Machine-specific files (`.venv/`, recordings, `calibration.local.json`) are git-ignored.
Test clips live in the team Google Drive, not in the repo.

## Remote access to the Pi

- **Terminal:** `ssh vision@southpaw.local` (works at home and on the iPhone hotspot); at home also `ssh vision@southpaw`
- **Desktop (needed for windows):** connect.raspberrypi.com → `southpaw` → Screen sharing
- **Fallback:** Raspberry Pi Connect → Remote shell → `hostname -I` to find the IP

## Troubleshooting

| Problem | Fix |
|---|---|
| `Illegal instruction` / `compiled with aes enabled` | Wrong MediaPipe version — recreate the venv with Python 3.12 and `mediapipe==0.10.18` |
| `Could not open webcam 0` | Check `v4l2-ctl --list-devices`; try `--source 1` |
| Window error mentioning Qt or xcb | `QT_QPA_PLATFORM=xcb python ...` |
| FPS much lower than ~9 | `vcgencmd get_throttled` — fix power/cooling if not `0x0` |
| "Person: NOT FOUND" during fast punches | More light; keep head, shoulders and fists in frame |
| Pi unreachable on Wi-Fi after a while | Wi-Fi power saving: `sudo iw wlan0 set power_save off` |
| `Address already in use` from `udp_listener.py` | Another listener (or the hub) already has port 5005 |
| Recorder reports < 27 fps | Close the cue window's other apps; `v4l2-ctl -d /dev/video0 -c exposure_dynamic_framerate=0` |

## Credits

- **Pose model:** MediaPipe Pose Landmarker (BlazePose GHUM 3D, lite), Google — Apache 2.0.
  <https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker>
- **Paper:** V. Bazarevsky et al., "BlazePose: On-device Real-time Body Pose tracking", 2020.
- **Libraries:** MediaPipe (Apache 2.0), OpenCV (Apache 2.0), NumPy, Matplotlib.

Our own work: the capture pipeline, punch detection and classification rules, Pi-speed
simulation, evaluation tools, contact detection, calibration and the hub messaging.
