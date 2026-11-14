"""
sender.py - Southpaw OS, computer vision (Member D)

Sends vision messages to the hub (Member C): one JSON object per UDP datagram,
to 127.0.0.1:5005 (protocol/PROTOCOL.md, "Pi-local inputs").

Why UDP:
  * fire-and-forget: vision never waits for the hub, and never crashes if the hub is
    off (the game must work without vision, and vision must work without the hub)
  * message boundaries are kept (one datagram = one message, no framing needed)
  * on the same Pi (127.0.0.1) packets are not lost in practice

Messages:

  guard:   {"v":1, "src":"vision", "kind":"guard",
            "t": 1731.420,     capture time of the frame (time.monotonic() seconds; same clock as the hub)
            "age_ms": 142,     how old the frame was when this was sent
            "L": true,         left hand up in guard? true / false / null (can't tell)
            "R": false}        right hand, same
           sent for every frame the pose model processes (~9 a second on the Pi)

  status:  {"v":1, "src":"vision", "kind":"status", "t": 1732.0, "fps": 8.9,
            "person": true, "camera": true}        sent every heartbeat_interval_s
"""

import json
import socket
import time

PROTOCOL_VERSION = 1


class HubSender:
    def __init__(self, host="127.0.0.1", port=5005, enabled=True):
        self.address = (host, int(port))
        self.enabled = enabled
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if enabled else None
        self.sent = 0
        self.errors = 0

    def _send(self, message):
        """Send one JSON message. Never raises: a missing hub must not stop vision."""
        if not self.enabled:
            return False
        try:
            self.sock.sendto(json.dumps(message, separators=(",", ":")).encode("utf-8"), self.address)
            self.sent += 1
            return True
        except OSError:
            # e.g. network down, or (on some systems) "connection refused" when nothing listens
            self.errors += 1
            return False

    def guard(self, t_capture, hands, now=None):
        """Send one frame's guard state. hands = {"L": HandGuard, "R": HandGuard} from guard.py."""
        now = time.monotonic() if now is None else now
        message = {
            "v": PROTOCOL_VERSION, "src": "vision", "kind": "guard",
            "t": round(t_capture, 3),
            "age_ms": max(0, int(round((now - t_capture) * 1000))),
            "L": hands["L"].up,
            "R": hands["R"].up,
        }
        self._send(message)
        return message

    def status(self, fps, person, camera_ok=True, now=None):
        """Heartbeat: tells the hub that vision is alive and how well it's running."""
        message = {
            "v": PROTOCOL_VERSION, "src": "vision", "kind": "status",
            "t": round(time.monotonic() if now is None else now, 3),
            "fps": round(fps, 1), "person": bool(person), "camera": bool(camera_ok),
        }
        self._send(message)
        return message

    def close(self):
        if self.sock:
            self.sock.close()
