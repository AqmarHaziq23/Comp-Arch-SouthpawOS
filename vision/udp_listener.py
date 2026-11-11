"""
udp_listener.py - Southpaw OS, computer vision (Member D)

A stand-in for the hub: listens on the vision port and prints every message it gets.
Use it to test vision_main.py without Member C's hub, and to measure the delay from
"punch peak captured" to "hub received it".

  python udp_listener.py                  # listen on 127.0.0.1:5005 (vision on the same Pi)
  python udp_listener.py --host 0.0.0.0   # accept from other machines (vision on a laptop)

Only one program can listen on a port at a time: stop this before starting the real hub.

------------------------------------------------------------------------------------------
For Member C - receiving vision events inside an asyncio hub (bleak already uses asyncio):

    import asyncio, json, time

    class VisionProtocol(asyncio.DatagramProtocol):
        def __init__(self, on_message):
            self.on_message = on_message
        def datagram_received(self, data, addr):
            try:
                msg = json.loads(data)
            except ValueError:
                return                                     # ignore anything that isn't JSON
            msg["recv_t"] = time.monotonic()
            self.on_message(msg)                           # e.g. put it in the fusion queue

    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: VisionProtocol(handle_vision),
                                        local_addr=("127.0.0.1", 5005))

Same Pi: msg["t"] is comparable with the hub's time.monotonic().
Another machine: use msg["recv_t"] - msg["age_ms"] / 1000 instead.
------------------------------------------------------------------------------------------
"""

import argparse
import json
import socket
import time


def main():
    parser = argparse.ArgumentParser(description="Print vision messages (fake hub)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--quiet-status", action="store_true", help="hide heartbeat messages")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    print(f"Listening on udp://{args.host}:{args.port} (Ctrl+C to stop)")
    punches, delays = 0, []

    try:
        while True:
            data, addr = sock.recvfrom(4096)
            now = time.monotonic()
            try:
                msg = json.loads(data)
            except ValueError:
                print(f"Not JSON from {addr}: {data[:80]!r}")
                continue
            if msg.get("kind") == "punch":
                punches += 1
                # Delay from the peak frame being captured to arriving here (same machine only)
                delay_ms = (now - msg["t"]) * 1000 if addr[0] in ("127.0.0.1", "::1") else msg.get("age_ms", 0)
                delays.append(delay_ms)
                print(f"[{now:10.2f}] PUNCH #{punches}: {msg['hand']} {msg['type']:<8} conf {msg['conf']:.2f} "
                      f"contact {msg['contact']}  | peak -> received {delay_ms:.0f} ms")
            elif msg.get("kind") == "status":
                if not args.quiet_status:
                    print(f"[{now:10.2f}] status: {msg.get('fps')} fps, person {msg.get('person')}, "
                          f"camera {msg.get('camera')}")
            else:
                print(f"[{now:10.2f}] other: {msg}")
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        if delays:
            delays.sort()
            print(f"\n{punches} punches; peak -> received delay: median {delays[len(delays) // 2]:.0f} ms, "
                  f"max {delays[-1]:.0f} ms")


if __name__ == "__main__":
    main()
