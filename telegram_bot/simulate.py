"""
simulate.py - Southpaw OS Telegram bot

Pretends to be the NFC READER and the HUB, so you can test the bot on its own.
Sends exactly the UDP messages the real programs send:

  python simulate.py tap 04A1B2C3            # NFC reader: "a card was tapped" (use this to /link a card)
  python simulate.py round 04A1B2C3          # hub: "a round finished" with sample_round.json
  python simulate.py round 04A1B2C3 --random # same, with randomised numbers (each round looks different)
  python simulate.py demo 04A1B2C3           # tap, wait 3 s, then finish a round

For a whole realistic game (3 rounds from the hub's own simulator), run in hub/:
  python -m southpaw play --sim --card 04A1B2C3 --games 1

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
    """Send one JSON message to the bot, exactly like the NFC reader / hub will."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(message).encode("utf-8"), (host, port))
    sock.close()
    text = json.dumps(message)
    print(f"Sent to udp://{host}:{port}: {text[:120]}{'...' if len(text) > 120 else ''}")


def randomise(round_data):
    """Vary the sample numbers so repeated tests don't all look the same.
    Uses the hub's scoring (game.py): 10 per correct punch, 5 per landed punch, 10 per full combo."""
    data = copy.deepcopy(round_data)
    thrown_total = landed_total = 0
    for stats in data.get("by_type", {}).values():
        stats["thrown"] = random.randint(2, 8)
        stats["landed"] = random.randint(stats["thrown"] // 2, stats["thrown"])
        thrown_total += stats["thrown"]
        landed_total += stats["landed"]
    if data.get("by_type"):
        data["thrown"], data["landed"] = thrown_total, landed_total
    if "combos_called" in data:
        data["combos_called"] = random.randint(5, 8)
        data["combos_hit"] = random.randint(data["combos_called"] // 3, data["combos_called"])
        data["best_streak"] = random.randint(min(1, data["combos_hit"]), data["combos_hit"])
    if "score" in data:
        correct = int(thrown_total * random.uniform(0.7, 1.0))
        data["score"] = correct * 10 + landed_total * 5 + data.get("combos_hit", 0) * 10
    if "hr_bpm" in data:
        data["hr_bpm"] = random.randint(120, 170)
    return data


def main():
    parser = argparse.ArgumentParser(description="Pretend to be the NFC reader (card taps) and hub (finished rounds)")
    parser.add_argument("action", choices=["tap", "round", "demo"])
    parser.add_argument("uid", help="card UID in hex, e.g. 04A1B2C3")
    parser.add_argument("--file", default="sample_round.json", help="round stats to send")
    parser.add_argument("--random", action="store_true", help="randomise the sample numbers")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5007)
    args = parser.parse_args()

    if args.action in ("tap", "demo"):
        # Same message the NFC reader sends to the hub (protocol: {"uid": ...})
        send({"uid": args.uid}, args.host, args.port)
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
