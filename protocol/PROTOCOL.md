# SouthpawOS Message Protocol — v1.3

Owner: **Haziq (Member C)**. Only the owner changes this file. Bump the version
and post in the group chat when you do.

Code that implements this spec: `hub/southpaw/protocol.py` (Pi side).
Firmware must match it byte for byte.

## Transport

- Boards talk to the Pi over BLE using the **Nordic UART Service (NUS)**, which
  the TinyCircuits STBLE UART example already sets up.
  - Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
  - Board → Pi (notify) `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
  - Pi → board (write)  `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- **One message per BLE packet, max 20 bytes including the trailing `\n`.**
- Messages are ASCII, comma-separated, and **end with `\n`**. The Pi
  reassembles lines, so a message split across two packets still works, but
  don't rely on it.
- Board timestamps are `millis()` since the board started. The Pi converts
  them to its own clock (see Clock sync).

## Codes

**Punch type** (1 letter, to fit in 20 bytes):
`J` = jab, `C` = cross, `H` = hook, `U` = uppercut, `X` = unknown.
Send `X` when the board detected a punch but can't tell which type (for
example before the classifier exists). An `X` punch still counts as thrown and
can still land, but never matches a called punch unless the camera supplies
the type.
(The Pi also accepts the full words `JAB`, `CROSS`, `HOOK`, `UPPERCUT` and
`UNKNOWN`, but firmware should send the letters.)

**Hand:** `L` or `R`.

**Combo numbers** (standard boxing numbering, orthodox stance):

| # | Punch | Hand (orthodox) | Hand (southpaw) |
|---|-------|-----------------|-----------------|
| 1 | Jab | L | R |
| 2 | Cross | R | L |
| 3 | Lead hook | L | R |
| 4 | Rear hook | R | L |
| 5 | Lead uppercut | L | R |
| 6 | Rear uppercut | R | L |

## Messages

| From → to | Message | Example | Meaning |
|-----------|---------|---------|---------|
| Wrist → Pi | Punch | `P,L,H,48213,11` | Left hand, hook, board time 48213 ms, peak 11 g |
| Wrist → Pi | Raw window (training mode only) | `R,...` | Raw samples for the training set (format owned by Member A) |
| Bag → Pi | Impact | `I,48260,72` | Bag hit at 48260 ms, strength 72/100 |
| HR station → Pi | Heart rate | `H,142,1` | 142 BPM; `1` = good reading, `0` = unreliable. Sent about every 2 s while a finger is on the sensor |
| Pi → Bag | Combo | `C,1-2-3` | Show this combo (jab, cross, lead hook) and play the cue |
| Pi → Bag | Sound | `S,HIT` | `BELL`, `WARN`, `HIT` or `MISS` |
| Pi → Bag | Mode | `M,REST` | `ROUND`, `REST` (heart-rate prompt) or `SUMMARY` |
| Pi → any board | Clock ping | `T` | Board must reply immediately with its `millis()` |
| Any board → Pi | Clock reply | `T,48100` | Board time when the ping arrived |

Pi-local inputs (not BLE, so no 20-byte limit). Both are one JSON object per
UDP datagram sent to `127.0.0.1`:

| From | Port | Example |
|------|------|---------|
| Vision process | 5005 | `{"t": 1731.42, "hand": "L", "type": "H", "contact": true, "conf": 0.8}` |
| NFC reader | 5006 | `{"uid": "04A2B91C"}` |

`t` is Pi time in seconds from `time.monotonic()` on the same Pi.

### Punch fields

`P,<hand>,<type>,<board_ms>,<peak_g>`

- `board_ms`: `millis()` at the **peak sample**, the moment closest to contact
  with the bag. This is what gets matched against the bag's impact time.
- `peak_g`: total acceleration at the peak, `sqrt(x² + y² + z²)` in g, rounded
  to the nearest whole g. Can reach about 27, because each axis clips at 16 g.
- The message is sent once the board has the samples after the peak, so it
  arrives about 250–310 ms after `board_ms`. The hub waits long enough for it.
- Punches thrown while the board is disconnected are dropped, not queued.
- Wrist boards ignore everything from the Pi except `T`. The Pi doesn't send
  them anything else.

## Boards

| Name in `boards.toml` | Board | Owner |
|-----------------------|-------|-------|
| `wristL`, `wristR` | Forearm boards | Member A |
| `bag` | Bag station (screen, impacts, buzzer) | Member B |
| `hr` | Heart-rate station (pulse sensor) | ZH |

The hub finds boards **by address only**, set in `hub/boards.toml`. The
advertised name (for example `P1D-WRIST-L`) is ignored, so naming the boards
is optional and just helps when scanning. A board's address stays the same
across power cycles.

The heart-rate station doesn't need any commands from the Pi. It just sends
`H` while someone's finger is on the sensor, and the hub uses the readings it
gets during rest periods. Like every board, it must answer `T`.

## Clock sync

When a board connects, the Pi sends `T` a few times. For each ping it records
the send time `t0` and the receive time `t1` of the `T,<ms>` reply, and
estimates

```
offset = (t0 + t1) / 2 - board_ms / 1000
pi_time = board_ms / 1000 + offset
```

It keeps the sample with the shortest round trip (the least jitter). Firmware
must answer `T` straight away, before any slow work such as drawing the screen.

## Changelog

- **v1.3 (11 Nov 2026):** added punch type `X` (unknown), wrote down what
  `board_ms` and `peak_g` mean and the wrist's send delay, and that boards are
  found by address.
- **v1.2 (10 Nov 2026):** heart rate now comes from its own station (`hr`)
  instead of the bag board.
- **v1.1 (2 Nov 2026):** punch types shortened to one letter and combos sent as
  numbers, because `P,L,UPPERCUT,1234567,16` (23 bytes) and
  `C,UPPERCUT-UPPERCUT-HOOK` overflowed the 20-byte BLE packet. Added the `\n`
  terminator, the clock reply and the UDP ports for vision and NFC.
- **v1 (1 Nov 2026):** first version, from the project plan.
