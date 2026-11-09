# firmware/wrist: forearm boards (owner: Yue Ming, A)

Two sketches for a TinyScreen+ with the Accelerometer TinyShield (and, for the game, the ST Bluetooth TinyShield):

| Sketch | Use it for | What it does |
|---|---|---|
| `wrist_logger/` | Recording training data (P0) | Streams `board_ms,x,y,z` over USB at 200 Hz, ±16 g. Nothing else. |
| `southpaw_wrist/` | The game (P1) | Samples at 200 Hz, detects punches on the board, classifies them (stub for now), shows status on the screen and sends the protocol's punch message over Bluetooth. |

Messages to the Pi follow `protocol/PROTOCOL.md`: send `P,L,H,48213,11\n` and reply to `T` immediately.

```
firmware/wrist/
├── wrist_logger/wrist_logger.ino     # P0 logger: ±16 g, 200 Hz, prints "board_ms,x,y,z"
└── southpaw_wrist/                   # game firmware
    ├── southpaw_wrist.ino            # main loop: ties the modules together, never blocks
    ├── config.h                      # HAND, sample rate, detector/feature constants
    ├── sampler.*                     # fixed-rate BMA250 reads into a 1.3 s ring buffer
    ├── detector.*                    # threshold + peak search + cooldown
    ├── features.*                    # integer-only punch features
    ├── classifier.*                  # returns UNKNOWN for now: Yue Ming's rules go here
    ├── display.*                     # status screen, one changed line per loop pass
    ├── ble.*                         # Nordic UART over the ST BLE shield, T clock reply
    └── battery.*                     # supply voltage
training/
├── tools/log_accel.py                # records one labelled CSV per run
├── tools/plot_accel.py               # plots CSVs (x, y, z in g)
├── tools/segment_punches.py          # same detector + features as the board, on CSVs
├── tools/ble_listen.py               # connect to a board from a laptop, print its messages
└── data/raw/                         # recordings go here (created automatically)
```

## 1. One-time setup (Arduino IDE)

1. Install **Arduino IDE 2** from arduino.cc.
2. **File → Preferences → Additional Boards Manager URLs**, paste:
   `http://files.tinycircuits.com/ArduinoBoards/package_tinycircuits_index.json`
3. **Tools → Board → Boards Manager**: install **Arduino SAMD Boards (32-bits ARM Cortex-M0+)**, then **TinyCircuits SAMD Boards**.
4. **Sketch → Include Library → Manage Libraries**: install **TinyScreen**.
5. Download **BMA250.zip** from the [Accelerometer TinyShield tutorial](https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/). Add it with **Sketch → Include Library → Add .ZIP Library**.
6. For `southpaw_wrist` only: download **[STBLE.zip](https://github.com/TinyCircuits/TinyCircuits-TinyShield-BLE-ASD2116/raw/master/examples/STBLE.zip)** (linked from the [Bluetooth TinyShield tutorial](https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/); it isn't in the Library Manager) and add it the same way.
7. Stack the board: TinyScreen+ on top, then the Accelerometer TinyShield and the Bluetooth TinyShield.
   - Mark which way the board faces on your wrist with tape. **Always strap it the same way.**

## 2. Upload the P0 logger

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

`southpaw_wrist` also streams the same `board_ms,x,y,z` lines over USB (`USB_STREAM_RAW` in `config.h`), so `log_accel.py` can record from it too. Its `#` debug lines are ignored by the tools.

### Cut recordings into punches

```bash
python training/tools/segment_punches.py                     # every CSV in training/data/raw/
python training/tools/segment_punches.py training/data/raw/ym_L_jab_*.csv
python training/tools/segment_punches.py --set DETECT_THRESHOLD_COUNTS=128   # try a value without re-flashing
```

It runs **the same detector and features as the board** (constants read from `southpaw_wrist/config.h`, integer maths identical to `detector.cpp` / `features.cpp`), then:
- prints punches found per file. If you threw 10 jabs and it finds 7 or 13, tune the threshold or cooldown before trusting the data;
- writes `training/data/features.csv`, one row per punch, with the label, hand and person taken from the file name;
- saves `<file>_punches.png` next to each CSV, one panel per punch, so you can check the cuts by eye (PNGs are gitignored).

If you change the detector or features in the firmware, change `segment_punches.py` the same way.

## 4. The game firmware: `southpaw_wrist`

### Set the hand

Open `southpaw_wrist/config.h` and set

```cpp
#define HAND 'L'      // 'L' for the left wrist, 'R' for the right
```

`HAND` sets the Bluetooth name (`P1D-WRIST-L` / `P1D-WRIST-R`), the hand letter in every punch message, and a per-hand public address. Flash one board with `'L'` and the other with `'R'`.

### Build and upload

Arduino IDE: open `southpaw_wrist/southpaw_wrist.ino`, choose **TinyScreen+** and its port, and click **Upload**. Or with arduino-cli (see the end of this file for setup):

```bash
arduino-cli compile --fqbn TinyCircuits:samd:tinyscreen firmware/wrist/southpaw_wrist
arduino-cli upload  --fqbn TinyCircuits:samd:tinyscreen -p <port> firmware/wrist/southpaw_wrist
```

The warnings from inside the STBLE library and the TinyCircuits core (`__packed__` attribute ignored, `digitalPinToInterrupt` redefined) are expected; the sketch's own files compile without warnings.

### What you should see

The screen:

```
P1D-WRIST-L
Last: UNKNOWN          (punch type of the last punch; UNKNOWN until the classifier exists)
Punches: 12
Peak: 9 g
BLE: connected         (or "not connected")
Batt: 3.20 V
```

`NO ACCEL SHIELD!` on the first line means the accelerometer isn't answering (it retries every second).
The battery value is the chip's supply voltage, so it stays near 3.2–3.3 V and only drops when the battery is nearly flat.

USB (Serial Monitor at 115200, or any program that opens the port):
- `board_ms,x,y,z` raw samples, 200 per second;
- `# southpaw_wrist name=P1D-WRIST-L hand=L ble=C2:0D:12:83:DF:85 ...` once when the port opens (`ble=` is the address the Pi will see);
- `# punch n=3 peak_ms=... peak_g=9 dom=-z burst_ms=220 tilt=45 ... type=X` for each punch, then `# ble tx P,L,X,48213,9` (or `# ble not sent (not connected) ...`);
- `# stats ...` and `# ble ...` every 5 s: sample count (should be 1000), worst sample lateness, punches, knocks ignored, Bluetooth sends and failures.

### Settings in `config.h`

| Constant | Default | Meaning |
|---|---|---|
| `DETECT_THRESHOLD_COUNTS` | 96 (3 g) | Trigger when total acceleration rises to this (gravity included). **Starting guess: tune with recordings.** |
| `PEAK_SEARCH_MS` | 100 | After a trigger, the peak is the largest sample in this time. |
| `COOLDOWN_MS` | 400 | No new trigger until this long after the peak. 300 double-counted: a punch's stop/retract burst came up to 250 ms after the peak. |
| `WINDOW_PRE_MS` / `WINDOW_POST_MS` | 150 / 250 | Samples handed to the features step, around the peak. The punch message goes out about `WINDOW_POST_MS` after the peak. |
| `MIN_BURST_MS` | 20 | Shorter bursts are knocks (putting the board down), not punches. |
| `BURST_THRESHOLD_COUNTS`, `GRAVITY_AVG_MS` | 48 (1.5 g), 50 | Feature settings: burst length and the before/after tilt averages. |

`segment_punches.py` reads these, so keep each as `constexpr <type> NAME = <integer>;` on one line.

### How it stays on time

Everything runs from `loop()` without `delay()`, `String` or heap allocation. A sample is due every 5 ms; a normal pass takes about 1 ms, and a pass that also redraws one screen line about 4 ms. Two things learned the hard way:
- **Don't use `if (!SerialUSB)` in the loop.** The core's check runs `delay(10)` every call. The sketch uses `SerialUSB.dtr()`.
- **Send each USB line with one `write()`.** Every `print()` call becomes its own USB packet and waits for the last one, so a line built from many `print()`s can take several ms.

## 5. Test Bluetooth without the Pi

```bash
pip install bleak
python training/tools/ble_listen.py --scan                 # nearby named devices; look for P1D-WRIST-L
python training/tools/ble_listen.py --hand L --ping 5      # connect, test the T clock reply like the hub does
python training/tools/ble_listen.py --hand L --log ble.jsonl --seconds 60
```

Every line the board sends is printed with a timestamp and checked with the hub's own parser (`hub/southpaw/protocol.py`), so `ok` means the Pi would accept it. If the laptop's Bluetooth can't connect, the **nRF Connect** phone app shows the same Nordic UART messages.

Tested on hardware (8–9 Nov):
- advertises as `P1D-WRIST-L`; the address (`C2:0D:12:83:DF:85` on this board) is the BlueNRG chip's own and **stays the same after a power cycle**, so it can go in the hub's `boards.toml`;
- connects from a Windows laptop (bleak), answers every `T` ping (33–91 ms round trip), and advertises again after a disconnect;
- stays connected for 10 minutes with sampling at exactly 1000 samples per 5 s, no resyncs, no failed sends.

Windows can keep the link open for about 10 s after `ble_listen.py` exits. The board doesn't advertise while it's connected, so a reconnect straight away may report "not found": wait a few seconds and try again.

**Not yet tested on hardware: punch messages over Bluetooth.** Shake the board while `ble_listen.py` is connected and check each `P,L,X,...` line arrives.

Known protocol gap: until the classifier exists every punch is sent as type `X` (unknown). Protocol v1.1 has no unknown type, so **the hub rejects these** (`ble_listen.py` shows `REJECTED by hub`). Waiting on Haziq to add one.

## Using Claude Code with this

Claude Code doesn't run *inside* the Arduino IDE. It runs in a terminal, or as an extension in VS Code and JetBrains IDEs. A setup that works well:

1. Open this repo in a terminal (or VS Code) and run `claude`.
2. Let Claude Code edit the sketches and the Python tools.
3. Keep the Arduino IDE open on the same `.ino` file to upload; it reloads the file after Claude Code changes it.

To let Claude Code compile and upload too, install **arduino-cli**:

```bash
arduino-cli config init
arduino-cli config add board_manager.additional_urls http://files.tinycircuits.com/ArduinoBoards/package_tinycircuits_index.json
arduino-cli core update-index
arduino-cli core install arduino:samd TinyCircuits:samd
arduino-cli lib install TinyScreen
arduino-cli config set library.enable_unsafe_install true   # needed for --zip-path; turn it off again after
arduino-cli lib install --zip-path BMA250.zip
arduino-cli lib install --zip-path STBLE.zip
arduino-cli config set library.enable_unsafe_install false
arduino-cli board listall | grep -i tiny          # the TinyScreen+ FQBN is TinyCircuits:samd:tinyscreen
arduino-cli board list                            # note the port
arduino-cli compile --fqbn TinyCircuits:samd:tinyscreen firmware/wrist/southpaw_wrist
arduino-cli upload  --fqbn TinyCircuits:samd:tinyscreen -p <port> firmware/wrist/southpaw_wrist
```

Claude Code needs to run on the laptop the board is plugged into, because it uses the USB port.

## Credits
- `wrist_logger` and the sampler in `southpaw_wrist` are based on TinyCircuits' Accelerometer TinyShield example (Laverena Wienclaw).
- `southpaw_wrist/ble.*` is based on TinyCircuits' ST BLE `UARTPassThrough` example (Ben Rose); `display.*` on the TinyScreen library example; `battery.*` on the TinyCircuits smartwatch example from the course site.
- Modified by Yue Ming (Team P1D). Every source is listed in `docs/CITATIONS.md` and in each file's header.
