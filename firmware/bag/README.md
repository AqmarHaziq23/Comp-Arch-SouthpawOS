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

Rules: no `delay()` (the BLE link needs `aci_loop()` every loop), every message
20 bytes or less and ending in `\n`. Start from **File → Examples → STBLE →
UARTPassThrough** for BLE and the TinyCircuits pulse sensor example for the
readings. Credit both in the sketch and in `docs/CITATIONS.md`.
