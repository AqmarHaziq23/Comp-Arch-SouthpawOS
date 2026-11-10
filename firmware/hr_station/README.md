# Heart-rate station firmware — ZH

A separate board with the pulse sensor. The player puts a finger on it during
the rest between rounds.

What it needs to do (see `protocol/PROTOCOL.md`):

- Send `H,<bpm>,<1|0>\n` about every 2 seconds while a finger is on the sensor
  (`1` = good reading, `0` = unreliable). Send nothing when there's no finger.
- Answer `T` straight away with `T,<millis>\n`.
- No `delay()`: the BLE link needs the loop to keep running.

Starting points: the STBLE **UARTPassThrough** example for BLE, and the
TinyCircuits pulse sensor example for the readings. Credit both in the sketch
and in `docs/CITATIONS.md`.
