# Bag station firmware — Member B

Arduino sketch for the bag board (screen, impacts, buzzer) as `bag.ino`.
Heart rate has moved to its own board, see `firmware/hr_station/`.

Messages must follow `protocol/PROTOCOL.md`. Keep everything non-blocking
(`millis()`, never `delay()`), answer `T` straight away, and end every message
with `\n`. Start from the STBLE **UARTPassThrough** example for the BLE part.
