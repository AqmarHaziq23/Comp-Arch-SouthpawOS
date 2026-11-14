"""
udp_listener.py - Southpaw OS, computer vision (Member D)

A stand-in for the hub: listens on the vision port and shows what vision sends.
Use it to test vision_main.py without Member C's hub.

  python udp_listener.py                  # listen on 127.0.0.1:5005 (vision on the same machine)
  python udp_listener.py --host 0.0.0.0   # accept from other machines (vision on a laptop)
  python udp_listener.py --every          # print every guard message, not just the changes

Only one program can listen on a port at a time: stop this before starting the real hub.
"""

import argparse
import json
import socket
import time


def word(up):
    return "UP" if up else ("down" if up is False else "?")


def main():
    parser = argparse.ArgumentParser(description="Print vision messages (fake hub)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--every", action="store_true", help="print every guard message")
    parser.add_argument("--quiet-status", action="store_true", help="hide heartbeat messages")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    print(f"Listening on udp://{args.host}:{args.port} (Ctrl+C to stop)")
    guard_msgs, delays, last = 0, [], None

    try:
        while True:
            data, addr = sock.recvfrom(4096)
            now = time.monotonic()
            try:
                msg = json.loads(data)
            except ValueError:
                print(f"Not JSON from {addr}: {data[:80]!r}")
                continue
            kind = msg.get("kind")
            if kind == "guard":
                guard_msgs += 1
                # Delay from frame capture to arriving here (only meaningful on the same machine)
                delays.append((now - msg["t"]) * 1000 if addr[0] in ("127.0.0.1", "::1") else msg.get("age_ms", 0))
                state = (msg.get("L"), msg.get("R"))
                if args.every or state != last:
                    print(f"[{now:10.2f}] guard  L {word(state[0]):<4}  R {word(state[1]):<4}  "
                          f"(frame -> here {delays[-1]:.0f} ms)")
                last = state
            elif kind == "status":
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
            print(f"\n{guard_msgs} guard messages; frame -> received delay: median {delays[len(delays) // 2]:.0f} ms, "
                  f"max {delays[-1]:.0f} ms")


if __name__ == "__main__":
    main()
