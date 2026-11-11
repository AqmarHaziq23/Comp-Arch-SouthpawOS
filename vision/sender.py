"""
sender.py - Southpaw OS, computer vision (Member D)

Sends vision events to the hub (Member C): one JSON object per UDP datagram.

Why UDP:
  * fire-and-forget: vision never waits for the hub, and never crashes if the hub is
    off (the game must work without vision, and vision must work without the hub)
  * message boundaries are kept (one datagram = one event, no framing needed)
  * on the same Pi (127.0.0.1) packets are not lost in practice; across Wi-Fi a lost
    vision event is harmless because vision is only a second opinion
  * moving vision to a laptop = change hub_host to the Pi's IP; nothing else changes

Messages (protocol v1 vision event + extra fields the hub may ignore):

  punch:   {"v":1, "src":"vision", "kind":"punch",
            "t": 1731.420,        capture time of the PEAK frame (time.monotonic(), seconds)
            "t_wall": 1794356123.5,  the same moment as time.time() (wall clock), if the hub uses that
            "t_start": 1731.21,   capture time of the last guard frame before the punch
            "age_ms": 142,        how old the peak frame was when this was sent
            "hand": "L", "type": "hook",          type: jab / cross / hook / uppercut
            "contact": true,      true / false, or null if the bag zone isn't calibrated
            "conf": 0.82}

  status:  {"v":1, "src":"vision", "kind":"status", "t": 1732.0, "fps": 8.9,
            "person": true, "camera": true}        sent every heartbeat_interval_s

Timing note for the hub: on the SAME Pi, "t" is directly comparable with the hub's own
time.monotonic(). If vision runs on ANOTHER machine, the clocks differ: use
(time the hub received it) - age_ms instead.
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

    def punch(self, event, contact, now=None):
        """Send a punch event (a PunchEvent from punch_detector.py). Returns the message."""
        now = time.monotonic() if now is None else now
        message = {
            "v": PROTOCOL_VERSION, "src": "vision", "kind": "punch",
            "t": round(event.t_peak, 3),
            # The same moment on the wall clock (time.time()), in case the hub uses that clock instead
            "t_wall": round(time.time() - (now - event.t_peak), 3),
            "t_start": round(event.t_start, 3),
            "age_ms": max(0, int(round((now - event.t_peak) * 1000))),
            "hand": event.hand,
            "type": event.type,
            "contact": contact,
            "conf": round(event.conf, 2),
        }
        self._send(message)
        return message

    def status(self, fps, person, camera_ok=True, now=None):
        """Heartbeat: tells the hub (and dashboard) that vision is alive and how well it's running."""
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
