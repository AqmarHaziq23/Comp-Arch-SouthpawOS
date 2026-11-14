# Telegram bot — Member D (Johanan)

Sends each player their boxing round results on Telegram after every round.
Runs as its own program on the Pi (like vision): if it stops, the game still works.

## How it works

```
 Player's phone                         Raspberry Pi
 ─────────────                  ┌───────────────────────────────────────────┐
  /start, /link  ─── Telegram ──►│  bot.py  ◄── UDP 127.0.0.1:5006 ── Hub (C) │◄── NFC reader, boards
  round summary  ◄── Telegram ───│   │                                       │
                                 │   └── players.db (card ↔ chat, rounds)    │
                                 └───────────────────────────────────────────┘
```

1. **Link once:** the player opens the bot, sends `/link`, then taps their NFC card on the Pi
   within 60 s. The bot saves *card UID → Telegram chat*.
2. **Each round:** player taps card → bot says "card scanned, punch the bag 5 times to start" →
   round → hub sends the stats → bot sends the summary to that player.

**Why players must link first:** Telegram bots can't message someone by @username. They may only
message people who have opened the bot and pressed Start, using a numeric chat id.

The bot uses **long polling** (it keeps asking Telegram for new messages), so it works on a phone
hotspot — no port forwarding or public address needed. The Pi just needs internet.

## Player commands

| Command | Does |
|---|---|
| `/start`, `/help` | Welcome + instructions |
| `/link` | Link an NFC card (tap it on the Pi within 60 s) |
| `/me` | Which card is linked |
| `/last` | Most recent round again |
| `/unlink` | Remove the link, stop receiving results |

## Messages from the hub (for Member C)

One JSON object per UDP datagram to `127.0.0.1:5006`:

```json
{"v":1, "src":"hub", "kind":"card",  "uid":"04A1B2C3"}
{"v":1, "src":"hub", "kind":"round", "uid":"04A1B2C3", "round":{ ...stats... }}
```

- Send `card` **every time a card is tapped** (the bot uses it for linking and the "card scanned" hello).
- Send `round` **when a round ends**. Every stat is optional; see `sample_round.json` for the fields the
  message lays out nicely (`duration_s`, `punches_thrown`, `punches_landed`, `by_type`, `combos_called`,
  `combos_completed`, `score`, `heart_rate_avg`, `heart_rate_max`). **Any other field is still shown**
  under "Other stats", so new data appears without code changes.
- Card UIDs can be written any way (`04:A1:B2:C3`, `04a1b2c3`): the bot normalises them.

Python for the hub side:

```python
import json, socket
bot_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)          # create once
def tell_bot(message):                                                 # never blocks, never fails loudly
    bot_sock.sendto(json.dumps(message).encode(), ("127.0.0.1", 5006))
tell_bot({"v": 1, "src": "hub", "kind": "card", "uid": uid})
tell_bot({"v": 1, "src": "hub", "kind": "round", "uid": uid, "round": stats})
```

## Setup

### 1. Create the bot (once)
In Telegram, open **@BotFather** → `/newbot` → name (e.g. *Southpaw OS*) → username ending in `bot`.
Copy the **token**. Treat it like a password.

### 2. Install
Laptop (PowerShell, in this folder):
```powershell
uv venv .venv
.venv\Scripts\activate
uv pip install -r requirements.txt
copy .env.example .env          # then open .env and paste your token
```
Pi:
```bash
cd ~/Comp-Arch-SouthpawOS/telegram_bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env      # paste your token
```

### 3. Run
```bash
python bot.py
```
It prints `Bot @yourbot is running. Listening for the hub on udp://127.0.0.1:5006`.

## Testing without the NFC reader or hub

Terminal 1: `python bot.py`. Terminal 2 pretends to be the hub:

```bash
# On your phone: open the bot, send /start, then /link. Within 60 s:
python simulate.py tap 04A1B2C3           # "tap" a pretend card -> "Card linked!"
python simulate.py demo 04A1B2C3          # tap + a round with random sample stats -> summary on your phone
python simulate.py round 04A1B2C3         # just a round, using sample_round.json
```

Logic tests (no Telegram needed): `python test_core.py`

## Files

| File | Purpose |
|---|---|
| `bot.py` | **Run this.** Telegram commands + listens for the hub |
| `core.py` | The logic: linking, card taps, round summaries |
| `formatting.py` | Builds the round-summary message |
| `storage.py` | Local SQLite database (`players.db`) |
| `simulate.py` | Pretends to be the hub (testing) |
| `sample_round.json` | Sample round stats (until the real data format is agreed) |
| `test_core.py` | Logic tests |
| `.env.example` | Settings template; copy to `.env` (git-ignored) |

## Security and privacy notes

- **Token:** only in `.env` (git-ignored). If it leaks, `/revoke` in @BotFather and update `.env`.
- **Hub port** is bound to `127.0.0.1`: only programs on the Pi itself can tell the bot about cards and rounds.
- **Linking needs the physical card** on the reader within 60 s of `/link`, so nobody can link a card
  remotely. (NFC UIDs *can* be cloned with the right tools: fine for a game, not for anything sensitive.)
- `players.db` holds Telegram chat ids and round history: personal data. It stays on the Pi and is git-ignored.
