"""
simulate.py - Southpaw OS Telegram bot

Pretends to be the HUB, so you can test the bot before the NFC reader and the real
round data exist. Sends the same UDP messages the hub will send later.

  python simulate.py tap 04A1B2C3            # "a card was tapped" (use this to /link a card)
  python simulate.py round 04A1B2C3          # "a round finished" with sample_round.json
  python simulate.py round 04A1B2C3 --random # same, with randomised numbers (each round looks different)
  python simulate.py demo 04A1B2C3           # tap, wait 3 s, then finish a round

The card UID can be anything in hex (e.g. 04A1B2C3) until the real reader is connected.
Run the bot (python bot.py) in another terminal first.
"""

import argparse
import copy
import json
import random
import socket
import time


def send(message, host, port):
    """Send one JSON message to the bot, exactly like the hub will."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(message).encode("utf-8"), (host, port))
    sock.close()
    print(f"Sent to udp://{host}:{port}: {json.dumps(message)[:120]}{'...' if len(json.dumps(message)) > 120 else ''}")


def randomise(round_data):
    """Vary the sample numbers so repeated tests don't all look the same (keeps them consistent)."""
    data = copy.deepcopy(round_data)
    thrown_total = landed_total = 0
    for stats in data.get("by_type", {}).values():
        stats["thrown"] = random.randint(12, 35)
        stats["landed"] = random.randint(int(stats["thrown"] * 0.6), stats["thrown"])
        thrown_total += stats["thrown"]
        landed_total += stats["landed"]
    if data.get("by_type"):
        data["punches_thrown"], data["punches_landed"] = thrown_total, landed_total
    if "combos_called" in data:
        data["combos_completed"] = random.randint(int(data["combos_called"] * 0.5), data["combos_called"])
    if "score" in data:
        data["score"] = landed_total * 10 + data.get("combos_completed", 0) * 50
    if "heart_rate_avg" in data:
        data["heart_rate_avg"] = random.randint(135, 165)
        data["heart_rate_max"] = data["heart_rate_avg"] + random.randint(10, 25)
    return data


def main():
    parser = argparse.ArgumentParser(description="Pretend to be the hub (card taps and finished rounds)")
    parser.add_argument("action", choices=["tap", "round", "demo"])
    parser.add_argument("uid", help="card UID in hex, e.g. 04A1B2C3")
    parser.add_argument("--file", default="sample_round.json", help="round stats to send")
    parser.add_argument("--random", action="store_true", help="randomise the sample numbers")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5006)
    args = parser.parse_args()

    if args.action in ("tap", "demo"):
        send({"v": 1, "src": "hub", "kind": "card", "uid": args.uid}, args.host, args.port)
    if args.action == "demo":
        print("...pretending to box for 3 seconds...")
        time.sleep(3)
    if args.action in ("round", "demo"):
        with open(args.file) as f:
            round_data = json.load(f)
        if args.random or args.action == "demo":
            round_data = randomise(round_data)
        send({"v": 1, "src": "hub", "kind": "round", "uid": args.uid, "round": round_data}, args.host, args.port)


if __name__ == "__main__":
    main()
