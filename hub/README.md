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

## With the real boards (Pi or laptop with Bluetooth)

1. Flash the TinyCircuits STBLE UART example onto a board.
2. `python -m southpaw scan` and note the `BlueNRG` addresses. Power boards on
   one at a time to tell them apart.
3. Copy `boards.example.toml` to `boards.toml` and fill in the addresses
   (`boards.toml` is gitignored).
4. `python -m southpaw ble boards.toml --listen` prints everything the boards
   send: use it for the first "hello" test.
5. `python -m southpaw ble boards.toml` runs the demo game.

In `ble` mode the hub also listens on UDP `127.0.0.1:5005` for vision events
and `:5006` for NFC taps (see `protocol/PROTOCOL.md`).

## Code map

| File | What it does |
|------|--------------|
| `southpaw/protocol.py` | Parses board messages and builds Pi commands (spec: `protocol/PROTOCOL.md`) |
| `southpaw/clock.py` | Converts each board's `millis()` to Pi time using `T` pings |
| `southpaw/fusion.py` | Matches punches with bag impacts (150 ms window) and vision events |
| `southpaw/sim.py` | Fake wrist, bag and camera, including a simulated player |
| `southpaw/ble.py` | Real BLE connections via bleak, with auto-reconnect |
| `southpaw/hub.py` | Glues it together, logs sessions, and has a demo game loop |

## For the rest of the team

- **Game engine (ZH):** build on `Hub`. Call `hub.send("bag", protocol.combo_msg([1, 2, 3]))`
  and `hub.resolve()` to get `FusedPunch` results. `run_demo` in `hub.py` is
  the throwaway version to replace.
- **Dashboard (Jovis):** read `logs/session-*.jsonl` or hook `Hub(on_punch=...)`.
  Socket.IO events will be agreed next.
- **Vision (Johanan):** send one JSON object per UDP datagram to port 5005.
- **Firmware (Yue Ming, bag pair):** match `protocol/PROTOCOL.md` exactly,
  end every message with `\n`, and answer `T` immediately.
