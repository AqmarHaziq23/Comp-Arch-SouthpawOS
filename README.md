# SouthpawOS

Throw the combo. Land the combo. TinyCircuit wrist sensors + Raspberry Pi + computer vision turn a heavy bag into a scored boxing game.

ICT1011 / INF1101 team exploration project, Team P1D.

## Repo layout

| Folder | What | Owner |
|--------|------|-------|
| `protocol/` | Message protocol between boards and the Pi (**read this first**) | Haziq (C) |
| `hub/` | Raspberry Pi hub: BLE, clock sync, fusion, simulator, game loop | Haziq, ZH (C) |
| `firmware/wrist/` | Forearm boards: punch detection and classification | Yue Ming (A) |
| `firmware/bag/` | Bag board: screen, impacts, buzzer, heart rate | B |
| `vision/` | MediaPipe pose tracking | Johanan (D) |
| `dashboard/` | Web dashboard and player pages | Jovis (E) |
| `training/` | Punch classifier training and export | A, C |
| `docs/CITATIONS.md` | Every source we use (required by the brief) | Everyone |

## Try it without hardware

```bash
cd hub
pip install -r requirements-dev.txt
python -m southpaw sim
```

See `hub/README.md` for details.

## Team rules

- `main` always runs. Work on a branch, open a pull request, merge after testing.
- Every input is optional: the game keeps running if the camera or heart-rate sensor drops out.
- Only Haziq changes the protocol. Bump the version and tell everyone.
- Credit every borrowed piece of code at the top of the file and in `docs/CITATIONS.md`.
