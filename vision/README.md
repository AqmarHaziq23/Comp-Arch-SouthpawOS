# Computer vision — Member D (Johanan)

MediaPipe pose tracking on the Pi. Runs as its own process and sends one JSON
object per UDP datagram to `127.0.0.1:5005` (format in `protocol/PROTOCOL.md`).
The game must keep working when this process is off.
