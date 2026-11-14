# Telegram bot — Member D (Johanan)

Sends each player their boxing round results on Telegram after every round.
Runs as its own program on the Pi (like vision): if it stops, the game still works.

## How it works

```
 Player's phone                         Raspberry Pi
 ─────────────                  ┌─────────────────────────────────────────────────────┐
  /start, /link  ─── Telegram ──►│  bot.py ◄──── UDP 5007: card tap ───── NFC reader   │
  round summary  ◄── Telegram ───│   │  ▲                                     │ UDP 5006 │
                                 │   │  └─ UDP 5007: round results ── Hub ◄───┘          │
                                 │   └── players.db (card ↔ chat, rounds)               │
                                 └─────────────────────────────────────────────────────┘
```

The NFC reader sends every tap to **both** the hub (5006, to pick the player) and the bot
(5007, for linking and the "card scanned" hello). The hub sends each finished round to the bot.

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

## Messages the bot receives

One JSON object per UDP datagram to `127.0.0.1:5007`:

| From | Message | When |
|---|---|---|
| NFC reader | `{"uid": "04A1B2C3"}` | Every card tap (same message the hub gets on 5006) |
| Hub | `{"v":1, "src":"hub", "kind":"round", "uid":"04A1B2C3", "round":{...}}` | After each round's rest (so the rest heart rate is included) |

- The hub side is `hub/southpaw/notify.py`. Guest rounds (no card) aren't sent.
- `round` uses the same names as the website upload: `round`, `rounds`, `duration_s`, `score`, `thrown`,
  `landed`, `combos_called`, `combos_hit`, `best_streak`, `by_type`, `hr_bpm`, `hr_good`
  (see `sample_round.json`). Every stat is optional, and **any other field is still shown** under
  "Other stats", so new data (e.g. guard stats) appears without changing the bot first.
- `{"kind": "card", "uid": ...}` is also accepted as a tap.
- Card UIDs can be written any way (`04:A1:B2:C3`, `04a1b2c3`): the bot normalises them.

## Setup

### 1. Create the bot (once)
In Telegram, open **@BotFather** → `/newbot` → name (e.g. *Southpaw OS*) → username ending in `bot`.
Copy the **token**. Treat it like a password.

### 2. Install
Windows (PowerShell, in this folder; any Python 3.9+):
```powershell
python -m venv .venv
.venv\Scripts\activate          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
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
It prints `Bot @yourbot is running. Listening for the hub on udp://127.0.0.1:5007`.

## Testing without the NFC reader or hub

Terminal 1: `python bot.py`. Terminal 2 pretends to be the NFC reader and the hub:

```bash
# On your phone: open the bot, send /start, then /link. Within 60 s:
python simulate.py tap 04A1B2C3           # "tap" a pretend card -> "Card linked!"
python simulate.py demo 04A1B2C3          # tap + a round with random sample stats -> summary on your phone
python simulate.py round 04A1B2C3         # just a round, using sample_round.json
```

A whole game from the **real hub code** with fake boards (in `hub/`, Python 3.10+):

```bash
python -m southpaw play --sim --card 04A1B2C3 --games 1    # 3 rounds -> 3 messages on your phone
```

Logic tests (no Telegram needed): `python test_core.py`

## Files

| File | Purpose |
|---|---|
| `bot.py` | **Run this.** Telegram commands + listens for the hub |
| `core.py` | The logic: linking, card taps, round summaries |
| `formatting.py` | Builds the round-summary message |
| `storage.py` | Local SQLite database (`players.db`) |
| `simulate.py` | Pretends to be the NFC reader and hub (testing) |
| `sample_round.json` | Sample round stats, in the hub's format |
| `test_core.py` | Logic tests |
| `.env.example` | Settings template; copy to `.env` (git-ignored) |

## Security and privacy notes

- **Token:** only in `.env` (git-ignored). If it leaks, `/revoke` in @BotFather and update `.env`.
- **Port 5007** is bound to `127.0.0.1`: only programs on the Pi itself can tell the bot about cards and rounds.
- **Linking needs the physical card** on the reader within 60 s of `/link`, so nobody can link a card
  remotely. (NFC UIDs *can* be cloned with the right tools: fine for a game, not for anything sensitive.)
- `players.db` holds Telegram chat ids and round history: personal data. It stays on the Pi and is git-ignored.
