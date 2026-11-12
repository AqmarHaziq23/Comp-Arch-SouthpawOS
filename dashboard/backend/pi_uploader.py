"""SouthpawOS - Pi side: send each round and the player's NFC card ID to the website.

Runs on the Raspberry Pi next to the hub. Only the Python standard library is
used, so there is nothing to install.

In the hub:

    from pi_uploader import Uploader

    up = Uploader("http://<server>:5000", token="<INGEST_TOKEN from the website's .env>")
    up.start_session(card_uid="04A2B91C")      # card tapped (None = guest)
    up.send_round(1, score=120, thrown=18, landed=14, hr_bpm=132, hr_good=True)   # after every round
    up.send_round(2, score=90, thrown=15, landed=10)
    up.end_session()

If the website cannot be reached, the round is kept in a file on the Pi and
sent again the next time anything is uploaded (or by `python pi_uploader.py
--flush`), so no round is lost when the network drops.

These calls wait for the network (up to `timeout` seconds). Inside the hub's
asyncio loop, call them with `await asyncio.to_thread(up.send_round, ...)`.

Try it without the hub:

    python pi_uploader.py --server http://localhost:5000 --token <token> --card 04A2B91C
"""
import argparse
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("pi_uploader")


class Uploader:
    def __init__(self, server, token, queue_file="unsent_rounds.jsonl", timeout=5):
        self.server = server.rstrip("/")
        self.token = token
        self.queue_file = Path(queue_file)
        self.timeout = timeout
        self.session = None       # the name of the session being played
        self.card_uid = None      # the player's NFC card ID, or None for a guest

    # ---------- player ----------
    def start_session(self, card_uid=None, session=None):
        """Call when a game starts. `card_uid` is the NFC card that was tapped (None = guest).
        Returns the session name; every round of this game is uploaded under it."""
        self.card_uid = "".join(str(card_uid).split()).upper() if card_uid else None
        self.session = session or time.strftime("pi-%Y%m%d-%H%M%S")
        return self.session

    def end_session(self):
        """Call when the last round is over. Has one more go at any rounds still waiting."""
        self.flush()
        self.session = self.card_uid = None

    # ---------- rounds ----------
    def send_round(self, round_no, score, thrown, landed, combos_called=None, combos_hit=None,
                   best_streak=None, hr_bpm=None, hr_good=True):
        """Upload one finished round. Returns True if the website has it now,
        False if it was kept on the Pi to be sent later."""
        if self.session is None:
            self.start_session()
        data = {"session": self.session, "round": round_no, "card": self.card_uid,
                "score": score, "thrown": thrown, "landed": landed,
                "combos_called": combos_called, "combos_hit": combos_hit, "best_streak": best_streak}
        if hr_bpm is not None:
            data["hr"] = {"bpm": hr_bpm, "good": bool(hr_good)}
        self._keep(data)                   # written to disk first, so a crash cannot lose it
        return self.flush() == 0

    def flush(self):
        """Try to send every round still waiting on the Pi. Returns how many are left."""
        waiting = self._waiting()
        left = []
        for i, data in enumerate(waiting):
            status, reply = self._post("/api/round", data)
            if status == 200:
                continue
            if status == 400:              # the website will never accept this one: drop it, loudly
                log.error("round refused by the website and dropped: %s  %s", reply, data)
                continue
            log.warning("upload failed (%s); %d round(s) kept on the Pi", reply, len(waiting) - i)
            left = waiting[i:]             # network or token problem: keep this and the rest, in order
            break
        self._rewrite(left)
        return len(left)

    # ---------- plumbing ----------
    def _post(self, path, data):
        req = urllib.request.Request(
            self.server + path, data=json.dumps(data).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, f"HTTP {e.code}: {e.read().decode(errors='replace').strip()}"
        except (urllib.error.URLError, OSError) as e:
            return 0, f"cannot reach {self.server}: {getattr(e, 'reason', e)}"

    def _waiting(self):
        if not self.queue_file.exists():
            return []
        return [json.loads(line) for line in self.queue_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _keep(self, data):
        with self.queue_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")

    def _rewrite(self, items):
        if not items:
            self.queue_file.unlink(missing_ok=True)
            return
        tmp = self.queue_file.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(d) + "\n" for d in items), encoding="utf-8")
        tmp.replace(self.queue_file)


def main():
    ap = argparse.ArgumentParser(description="Send a made-up session to the website, to test the link from the Pi.")
    ap.add_argument("--server", default=os.environ.get("SOUTHPAW_SERVER", "http://localhost:5000"))
    ap.add_argument("--token", default=os.environ.get("INGEST_TOKEN", ""))
    ap.add_argument("--card", help="NFC card ID to play as (leave out for a guest)")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--queue-file", default="unsent_rounds.jsonl")
    ap.add_argument("--flush", action="store_true", help="only send rounds still waiting on the Pi")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.token:
        ap.error("give --token (or set INGEST_TOKEN): the INGEST_TOKEN from the website's .env")
    up = Uploader(args.server, args.token, args.queue_file)

    if not args.flush:
        print(f"session {up.start_session(args.card)} as {up.card_uid or 'guest'} -> {up.server}")
        for n in range(1, args.rounds + 1):
            thrown = random.randint(12, 24)
            landed = random.randint(thrown // 2, thrown)
            sent = up.send_round(n, score=landed * 10, thrown=thrown, landed=landed,
                                 hr_bpm=random.randint(125, 155), hr_good=True)
            print(f"  round {n}: thrown {thrown}, landed {landed} -> {'uploaded' if sent else 'kept on the Pi'}")
        up.end_session()
    left = up.flush()
    print("all rounds are on the website" if left == 0 else f"{left} round(s) still waiting in {up.queue_file}")
    raise SystemExit(0 if left == 0 else 1)


if __name__ == "__main__":
    main()
