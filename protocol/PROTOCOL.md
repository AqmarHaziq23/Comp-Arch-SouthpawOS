# SouthpawOS Message Protocol — v1.1

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
`J` = jab, `C` = cross, `H` = hook, `U` = uppercut.
(The Pi also accepts the full words `JAB`, `CROSS`, `HOOK`, `UPPERCUT` while
firmware is being written, but firmware should send the letters.)

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
| Bag → Pi | Heart rate | `H,142,1` | 142 BPM; `1` = good reading, `0` = unreliable |
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

- **v1.1 (2 Nov 2026):** punch types shortened to one letter and combos sent as
  numbers, because `P,L,UPPERCUT,1234567,16` (23 bytes) and
  `C,UPPERCUT-UPPERCUT-HOOK` overflowed the 20-byte BLE packet. Added the `\n`
  terminator, the clock reply and the UDP ports for vision and NFC.
- **v1 (1 Nov 2026):** first version, from the project plan.
