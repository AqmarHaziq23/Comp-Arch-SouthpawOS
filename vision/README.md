# Computer vision — Member D (Johanan)

MediaPipe pose tracking on the Pi. Runs as its own process and sends one JSON
object per UDP datagram to `127.0.0.1:5005` (format in `protocol/PROTOCOL.md`).
The game must keep working when this process is off.

## What it does

The wrist boards are the main punch detector. Vision is a **second opinion**,
focused on the two things the accelerometers are worst at:

1. **Hook vs uppercut** — the accelerometer's most common mix-up.
2. **Contact** — did the fist actually reach the bag?

```
webcam ─► capture ─► pose model (33 body points) ─► tracker ─► punch detector ─► classifier ─┐
                                                                         contact check ──────┤
                                                                                             ▼
                                                    debug overlay              UDP sender ─► hub (Member C)
```

## Status

| Part | Status |
|---|---|
| Pi + webcam setup | ✅ Done |
| `pose_preview.py` — live skeleton, wrist trails, FPS | ✅ Working (~9 fps on the Pi 4) |
| Labelled test clips | ⏳ Next |
| Punch detector + classifier | ⏳ Planned |
| Bag-zone calibration + contact | ⏳ Planned |
| UDP sender + heartbeat | ⏳ Planned |

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
Python 3.12 just for this project.

Use the same versions on every machine so results match.

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

### On a Windows laptop (for testing, or as the backup vision machine)

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

## Running

```bash
python pose_preview.py                        # live window — run on the Pi desktop (Raspberry Pi Connect)
python pose_preview.py --no-display           # no window, prints FPS once a second (works over SSH)
python pose_preview.py --record test1.mp4     # also save the overlay video
python pose_preview.py --source clip.mp4      # run on a recorded clip instead of the webcam
```

Press **Q** in the window, or **Ctrl+C** in the terminal, to stop.

The first lines of output contain harmless MediaPipe warnings (`cpuinfo`, `Feedback manager`,
`NORM_RECT`) — ignore them.

## Remote access to the Pi

- **Terminal:** `ssh vision@southpaw`
- **Desktop (needed for the live window):** connect.raspberrypi.com → `southpaw` → Screen sharing.
  The browser view looks laggier than the script really is; trust the FPS in the overlay.

## Measured performance (Pi 4, lite model, 640×480)

| Measure | Value |
|---|---|
| Processing speed | ~8.9 fps |
| Pose model time | ~105 ms per frame |
| Frame age (capture → result) | ~120–140 ms |

At ~9 fps a punch (0.2–0.3 s) spans only 2–3 frames, so the classifier compares the
**start pose with the peak pose** (elbow angle, wrist position relative to the shoulder)
rather than following a smooth path. Re-measure with the hub running on the same Pi; if
FPS drops too far, run vision on a laptop instead (only `hub_host` in `config.json` changes).

## Files

| File | Purpose |
|---|---|
| `pose_preview.py` | Live pose preview + FPS test |
| `config.json` | Thresholds, camera settings, hub address — tune here, not in code |
| `requirements.txt` | Pinned packages (`mediapipe==0.10.18`) |
| `pose_landmarker_lite.task` | The pose model |

Machine-specific files (`.venv/`, recordings, `calibration.local.json`) are git-ignored.
Test clips live in the team Google Drive, not in the repo.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Illegal instruction` / `compiled with aes enabled` | Wrong MediaPipe version — recreate the venv with Python 3.12 and `mediapipe==0.10.18` |
| `Could not open webcam 0` | Check `v4l2-ctl --list-devices`; try `--source 1` |
| Window error mentioning Qt or xcb | `QT_QPA_PLATFORM=xcb python pose_preview.py` |
| FPS much lower than ~9 | `vcgencmd get_throttled` — fix power/cooling if not `0x0` |
| "Person: NOT FOUND" during fast punches | More light; keep head, shoulders and fists in frame |
| Pi unreachable on Wi-Fi after a while | Wi-Fi power saving: `sudo iw wlan0 set power_save off` |

## Credits

- **Pose model:** MediaPipe Pose Landmarker (BlazePose GHUM 3D, lite), Google — Apache 2.0.
  <https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker>
- **Paper:** V. Bazarevsky et al., "BlazePose: On-device Real-time Body Pose tracking", 2020.
- **Libraries:** MediaPipe (Apache 2.0), OpenCV (Apache 2.0).

Our own work: the capture pipeline, wrist tracking, punch detection and classification rules,
contact detection, calibration and the hub messaging.