# firmware/wrist: forearm boards (owner: Yue Ming, A)

P0 goal (Sat 7 – Sun 8 Nov): a TinyScreen+ with the Accelerometer TinyShield streams X/Y/Z over USB at about 200 readings per second, and a Python script saves labelled punches to CSV.

Later wrist sketches that talk to the Pi must follow `protocol/PROTOCOL.md`: send `P,L,H,48213,11\n` and reply to `T` immediately.

```
firmware/wrist/
└── wrist_logger/wrist_logger.ino   # board sketch: ±16g, 200 Hz, prints "board_ms,x,y,z"
training/
├── tools/log_accel.py              # records one labelled CSV per run
├── tools/plot_accel.py             # plots CSVs (x, y, z in g)
└── data/raw/                       # recordings go here (created automatically)
```

## 1. One-time setup (Arduino IDE)

1. Install **Arduino IDE 2** from arduino.cc.
2. **File → Preferences → Additional Boards Manager URLs**, paste:
   `http://files.tinycircuits.com/ArduinoBoards/package_tinycircuits_index.json`
3. **Tools → Board → Boards Manager**: install **Arduino SAMD Boards (32-bits ARM Cortex-M0+)**, then **TinyCircuits SAMD Boards**.
4. **Sketch → Include Library → Manage Libraries**: install **TinyScreen**.
5. Download **BMA250.zip** from the [Accelerometer TinyShield tutorial](https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/). Add it with **Sketch → Include Library → Add .ZIP Library**.
6. Stack the board: TinyScreen+ on top, Accelerometer TinyShield underneath. The Bluetooth shield can stay on; it isn't used yet.
   - Mark which way the board faces on your wrist with tape. **Always strap it the same way.**

## 2. Upload the sketch

1. Plug in the TinyScreen+ by micro-USB and slide its power switch **ON**.
2. **Tools → Board → TinyScreen+**, and **Tools → Port →** the entry labelled TinyScreen+.
   - No port showing? Your cable may be power-only, so try another.
3. Open `wrist_logger/wrist_logger.ino` and click **Upload**.
4. Open **Tools → Serial Monitor** at 115200 baud. You should see lines like `48213,12,-3,31`.
   - Resting flat, one axis should read about **±32** (= 1 g of gravity).
   - If you see `ERROR: no BMA250 detected`, re-seat the accelerometer shield.
5. **Close the Serial Monitor** before using the Python logger, because only one program can hold the port.

If the board stops accepting uploads, use bootloader mode:
1. Switch the board OFF and plug in USB.
2. Hold the button nearest the USB port while switching it ON.
3. The screen shows "Bootloader Mode". Upload again.

## 3. Record punches

```bash
pip install pyserial matplotlib
cd training/tools
python log_accel.py --list                                  # find the port if auto-detect fails
python log_accel.py --label jab --hand L --person ym        # 20 s recording, 3 s countdown
python log_accel.py --label cross --hand R --person ym --seconds 30
python plot_accel.py ../data/raw/ym_L_jab_*.csv
```

**How to record so the data is useful later:**
- **One punch type per file.** Throw about **10 punches per file, with 1–2 s of guard between each**, and do 3 files per type (≈30 of each).
- **Labels:** `jab`, `cross`, `hook`, `uppercut`, plus `idle` (guard, shuffling, no punches). The idle file teaches the detector what *not* to trigger on.
- **Same stance and mounting every time.** Switch the board to the right wrist (`--hand R`) for crosses and rear hooks and uppercuts.
- **Record 2–3 teammates** too (`--person ys`, `--person zh`, …), so the classifier isn't tuned only to you.
- Start shadowboxing. Light bag work comes later.
- **Plot every file.** A hard punch will spike towards the dotted ±16 g lines. Clipping there is expected; we classify on shape and timing, not peak force.

Recordings land in `training/data/raw/`. Commit them to the repo, because they become the training set for the classifier (`training/`, shared with Haziq).

## Using Claude Code with this

Claude Code doesn't run *inside* the Arduino IDE. It runs in a terminal, or as an extension in VS Code and JetBrains IDEs. A setup that works well:

1. Open this repo in a terminal (or VS Code) and run `claude`.
2. Let Claude Code edit `wrist_logger.ino` and the Python tools.
3. Keep the Arduino IDE open on the same `.ino` file to upload; it reloads the file after Claude Code changes it.

To let Claude Code compile and upload too, install **arduino-cli**:

```bash
arduino-cli config init
arduino-cli config add board_manager.additional_urls http://files.tinycircuits.com/ArduinoBoards/package_tinycircuits_index.json
arduino-cli core update-index
arduino-cli core install arduino:samd TinyCircuits:samd
arduino-cli board listall | grep -i tiny          # note the TinyScreen+ FQBN
arduino-cli board list                            # note the port
arduino-cli compile --fqbn <FQBN> firmware/wrist/wrist_logger
arduino-cli upload  --fqbn <FQBN> -p <port> firmware/wrist/wrist_logger
```

Claude Code needs to run on the laptop the board is plugged into, because it uses the USB port.

## Credits
- Sketch based on TinyCircuits' Accelerometer TinyShield example (link above).
- Modified by Yue Ming (Team P1D): ±16 g range, fixed-rate sampling, CSV output.
- Listed in `docs/CITATIONS.md`.
