# Bag board firmware — Ying Song and ZH

The TinyCircuits board behind the bag: screen, impact detection, buzzer and
the pulse sensor. Sketch goes here as `bag.ino`.

What it sends and receives (see `protocol/PROTOCOL.md`):

- **Impacts:** `I,<millis>,<strength 0-100>\n` the moment the bag is hit,
  timestamped when it happens, not when it's sent.
- **Heart rate:** `H,<bpm>,<1|0>\n` about every 2 seconds while a finger is on
  the pulse sensor (`1` = good reading). Nothing when there's no finger.
- **From the Pi:** `C,1-2-3` (show this combo), `S,BELL|WARN|HIT|MISS`
  (sound), `M,ROUND|REST|SUMMARY` (screen mode).
- **Clock:** answer `T` straight away with `T,<millis>\n`.

## What's in this folder

| File | What it does |
|------|--------------|
| `bag.ino` | Zong Han's punch tracker: hit detection, screen, buzzer, 60 s standalone game, plus **hub mode** |
| `ble.cpp`, `ble.h` | BLE link to the Pi. Yue Ming's wrist BLE module, renamed `P1D-BAG` and extended to receive `C`, `S` and `M` |

**Hub mode:** the board advertises as `P1D-BAG`. When the hub connects and
sends `M,ROUND` or a combo, the bag stops its own game and the hub runs it:
the screen shows the called combo (`1-2-3` with the punch names), the hub's
sounds play, and every hit goes to the Pi as `I,<millis at the peak>,<0-100>`
(100 = 8 g). `M,REST` shows "finger on the pulse sensor". If the hub
disconnects, the bag goes back to its standalone game.

**Heart rate:** `sendHeartRate(bpm, good)` is ready in `bag.ino`. Call it about
every 2 s from the pulse sensor code once that's added.

## Setup (Arduino IDE)

1. Boards Manager: **TinyCircuits SAMD Boards**, board **TinyScreen+**.
2. Library Manager: **FlashStorage** (by Various / cmaglie), **Wireling** (by TinyCircuits).
3. **BMA250** isn't in the Library Manager. Download `BMA250.zip` from the
   [Accelerometer TinyShield tutorial](https://learn.tinycircuits.com/Sensors/Accelerometer_TinyShield_Tutorial/)
   and add it with **Sketch → Include Library → Add .ZIP Library**.
4. **STBLE** library from the [BLE TinyShield tutorial](https://learn.tinycircuits.com/Communication/Bluetooth-Low-Energy_TinyShield_Tutorial/).
5. Stack: TinyScreen+ → BLE TinyShield → Wireling Adapter (+ accelerometer).
   Keep the buzzer on Wireling port 0.
6. Upload, open the Serial Monitor at 115200: it prints
   `BLE ready as P1D-BAG, address 02:80:E1:50:31:42`. That fixed address is
   already on the `bag` line of `hub/boards.example.toml`.

Rules: no `delay()` (the BLE link needs `aci_loop()` every loop), every message
20 bytes or less and ending in `\n`. Start from **File → Examples → STBLE →
UARTPassThrough** for BLE and the TinyCircuits pulse sensor example for the
readings. Credit both in the sketch and in `docs/CITATIONS.md`.
