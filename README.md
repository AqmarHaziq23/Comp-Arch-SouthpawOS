# SouthpawOS

Throw the combo. Land the combo. TinyCircuit wrist sensors + Raspberry Pi + computer vision turn a heavy bag into a scored boxing game.

ICT1011 / INF1101 team exploration project, Team P1D.

## Repo layout

| Folder              | What                                                                      | Owner         |
| ------------------- | ------------------------------------------------------------------------- | ------------- |
| `protocol/`         | Message protocol between boards and the Pi (**read this first**)          | Haziq (C)     |
| `hub/`              | Raspberry Pi hub: BLE, clock sync, fusion, simulator, game loop           | Haziq, ZH (C) |
| `firmware/wrist/`   | Forearm boards: punch detection and classification                        | Yue Ming (A)  |
| `firmware/bag/`     | Bag board: screen, impacts, buzzer, pulse sensor                          | Ying Song, ZH |
| `vision/`           | MediaPipe pose tracking: is the guard up?                                 | Johanan (D)   |
| `telegram_bot/`     | Telegram bot: link your NFC card, get each round's results                | Johanan (D)   |
| `nfc/`              | PN532 NFC card reader: tap your card to sign in                           | Johanan (D)   |
| `pi/`               | Pi setup: start the hub, bot, NFC reader and vision automatically at boot | Johanan (D)   |
| `dashboard/`        | Web dashboard and player pages                                            | Jovis (E)     |
| `training/`         | Punch classifier training and export                                      | A, C          |
| `docs/CITATIONS.md` | Every source we use (required by the brief)                               | Everyone      |

## Try it without hardware

```bash
cd hub
pip install -r requirements-dev.txt
python -m southpaw sim
```

See `hub/README.md` for details.

## Flashing the TinyCircuits boards

The folder READMEs (`firmware/wrist/README.md`, `firmware/bag/README.md`) give the original links to download each Arduino library from TinyCircuits, GitHub or the Library Manager. To save you from collecting them one by one, `SouthpawOS_Arduino_libraries/` holds all of them in one place, recently downloaded from those same official sources. They are the same libraries, just consolidated.

| Library folder          | Header           | Used by                  |
| ----------------------- | ---------------- | ------------------------ |
| `TinyScreen`            | `TinyScreen.h`   | bag, wrist               |
| `TinyCircuits_BMA250`   | `BMA250.h`       | bag, wrist, wrist logger |
| `STBLE`                 | `STBLE.h`        | bag, wrist               |
| `FlashStorage`          | `FlashStorage.h` | bag                      |
| `Wireling`              | `Wireling.h`     | bag                      |
| `TinyCircuits_MAX30101` | `MAX30101.h`     | bag (heart rate)         |

To use them, copy the library folders into `Documents\Arduino\libraries\` and restart the Arduino IDE. If you already installed one of them from the links, delete the old copy first so the IDE doesn't find two.

You still need the board package: add `http://files.tinycircuits.com/ArduinoBoards/package_tinycircuits_index.json` to the Boards Manager URLs, install **TinyCircuits SAMD Boards**, and pick **TinyScreen+**. `Wire` and `SPI` come with the board package. See each folder's README for which sketch goes on which board and its settings.

## Team rules

- `main` always runs. Work on a branch, open a pull request, merge after testing.
- Every input is optional: the game keeps running if the camera or heart-rate sensor drops out.
- Only Haziq changes the protocol. Bump the version and tell everyone.
- Credit every borrowed piece of code at the top of the file and in `docs/CITATIONS.md`.
