# Hub (Raspberry Pi) — Member C

Receives messages from the TinyCircuits boards over BLE, aligns their clocks,
decides which punches landed, and logs every session. The same code runs on a
laptop with the simulator, so nobody needs hardware to start.

## Quick start (any computer, no hardware)

```bash
cd hub
python -m venv .venv
# Windows: .venv\Scripts\activate    Mac/Linux/Pi: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m southpaw sim          # play 2 simulated rounds
python -m pytest                # run the tests
```

Sample output:

```
called 2-4-6    (RC RH RU    ) thrown RC* RH* RJ      correct 2/3  landed 2  +30
```

`*` means the punch landed (a bag impact backed it up). Every message is also
written to `logs/session-*.jsonl`, which is the training data, the debugging
record and the demo replay.

Options: `--rounds 3 --combos 5 --stance southpaw --no-vision --seed 1`.
Add `-v` before the command for debug logs: `python -m southpaw -v sim`.

## Play the game

```bash
python -m southpaw play --sim                     # whole game with fake boards and a fake player
python -m southpaw play --sim --card 04A2B91C     # ...who taps this NFC card first
python -m southpaw play boards.toml               # the real thing
python -m southpaw play boards.toml --rounds 3 --round-secs 30 --rest-secs 20 \
    --server http://<website>:5000                # + upload each round (token in INGEST_TOKEN)
```

The bag shows "tap your card, then hit the bag 5x". A card tap (NFC, UDP 5006)
picks the player; no tap = guest. Five bag hits within 6 s start the game.
Each round calls combos until the time is up, then a rest (heart rate), and the
round is uploaded with Jovis's `dashboard/backend/pi_uploader.py` and saved in
`southpaw.db`. Rounds that can't be uploaded wait in `logs/unsent_rounds.jsonl`.

Each round of a carded player also goes to Johanan's Telegram bot (`telegram_bot/`) over
UDP `127.0.0.1:5007` (`southpaw/notify.py`), which messages the card's owner. If the bot
isn't running the message is just lost; `--no-telegram` turns it off.

**Guard stats** (`southpaw/guard.py`): Johanan's vision process sends `{"kind": "guard", "L": true,
"R": false}` about 9 times a second (hands up in guard?). For each wrist punch, that hand's readings
from 0.4 s before to 0.6 s after the peak are left out, because punching isn't dropping your guard. Each
round then gets `guard_pct` (both hands together), `guard_pct_lead`, `guard_pct_rear` and `guard_drops` (a hand
down for 0.4 s or more). They go to the Telegram bot. Without vision there are no guard stats.
The simulator fakes guard messages too (`--no-vision` turns them off).

Scoring per combo: 10 per punch with the right hand (and the right type, unless
the wrist sent `X`), 5 per landed punch, +10 if the whole combo was right.

## With the real boards (Pi or laptop with Bluetooth)

You don't need the Pi for this: any laptop with Bluetooth works, including
Windows.

1. Flash **File → Examples → STBLE → UARTPassThrough** onto a board and open
   the Serial Monitor (the sketch waits for it before starting BLE). It should
   say `General Discoverable Mode`.
2. Make sure nothing else is connected to the board (turn off your phone's
   Bluetooth). A connected board stops advertising and won't show up.
3. `python -m southpaw scan` should list `P1D-WRIST-L`, `P1D-WRIST-R` and
   `P1D-BAG` at `02:80:E1:50:31:4C`, `...:52` and `...:42`.
4. Copy `boards.example.toml` to `boards.toml` (gitignored); the fixed
   addresses are already in it. Comment out boards you don't have.
5. `python -m southpaw ble boards.toml --listen` prints everything the boards
   send. Type `P,L,H,1234,10` in the Serial Monitor and it shows up here.
6. `python -m southpaw ble boards.toml` runs the demo game.

"no clock reply" warnings are expected with the UARTPassThrough example,
because it doesn't answer `T`. Real firmware must.

In `ble` mode the hub also listens on UDP `127.0.0.1:5005` for vision events
and `:5006` for NFC taps (see `protocol/PROTOCOL.md`).

## Database (players and scores)

Every `sim` or `ble` game is saved to `hub/southpaw.db` (SQLite, gitignored).
The session logs keep every raw message; the database keeps the summaries.

```bash
python -m southpaw leaderboard                 # best score per player (real games only)
python -m southpaw leaderboard --include-sim   # include simulator runs
python -m southpaw player 04A2B91C "Yue Ming"  # name an NFC card
```

Add `--no-db` to `sim` or `ble` to skip saving, or `--db other.db` to use
another file.

| Table | What's in it |
|-------|--------------|
| `players` | NFC card UID, name, stance. `guest` is used when nobody taps a card |
| `sessions` | Player, `ble`/`sim`/`replay`, start and end time, total score |
| `punches` | Session, round, hand, type (`J/C/H/U/X`), peak g, landed, impact strength, camera's type |
| `heart_rates` | Session, round, BPM and whether the reading was good, one per rest |

- **NFC (ZH):** send `{"uid": "04A2B91C"}` to UDP port 5006 before a game.
  The next session is saved under that card. Unknown cards are added
  automatically as "Player B91C" and can be renamed later.
- **Dashboard (Jovis):** only the hub writes. Open the file read-only so you
  can't lock it: `sqlite3.connect("file:southpaw.db?mode=ro", uri=True)`.
  `Store.leaderboard()` and `Store.session_summary(id)` in `southpaw/db.py`
  show the queries.

## Code map

| File | What it does |
|------|--------------|
| `southpaw/protocol.py` | Parses board messages and builds Pi commands (spec: `protocol/PROTOCOL.md`) |
| `southpaw/clock.py` | Converts each board's `millis()` to Pi time using `T` pings |
| `southpaw/fusion.py` | Matches punches with bag impacts (150 ms window) and vision events |
| `southpaw/sim.py` | Fake wrist, bag and camera, including a simulated player |
| `southpaw/ble.py` | Real BLE connections via bleak, with auto-reconnect |
| `southpaw/hub.py` | Glues it together, logs sessions, and has a demo game loop |
| `southpaw/db.py` | SQLite store: players, sessions, punches, heart rates, leaderboard |

## For the rest of the team

- **Game engine (ZH):** build on `Hub`. Call `hub.send("bag", protocol.combo_msg([1, 2, 3]))`
  and `hub.resolve()` to get `FusedPunch` results. `run_demo` in `hub.py` is
  the throwaway version to replace.
- **Dashboard (Jovis):** read `logs/session-*.jsonl` or hook `Hub(on_punch=...)`.
  Socket.IO events will be agreed next.
- **Vision (Johanan):** send one JSON object per UDP datagram to port 5005.
- **Firmware (Yue Ming, bag pair):** match `protocol/PROTOCOL.md` exactly,
  end every message with `\n`, and answer `T` immediately.
