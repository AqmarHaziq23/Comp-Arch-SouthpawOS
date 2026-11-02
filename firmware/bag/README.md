# Bag station firmware — Member B

Arduino sketch for the bag board (screen, impacts, buzzer, pulse sensor) as `bag.ino`.
Messages must follow `protocol/PROTOCOL.md`. Keep everything non-blocking (`millis()`, never `delay()`).
