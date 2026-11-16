# SouthpawOS frontend - website and database

Flask + Chart.js, with its own database. It runs under Docker Compose (website + Postgres).
The Pi uploads each finished round to it (the sender is in `../backend`), and the website reads from that database.

    Pi (hub)  --POST /api/round after every round-->  web (Flask)  <-->  db (Postgres)
                                                          ^
                                               coaches and athletes log in

## Run

**The real setup (Docker Compose):**

    cp .env.example .env         # then change the three secrets inside
    docker compose up --build    # website on http://localhost:5000, database kept in the "dbdata" volume

**On a laptop, no Docker (fake Pi, demo data, local SQLite file `demo.db`):**

    pip install -r requirements.txt
    python app.py --sim          # add --speed 3 to run the fake rounds faster
    python tests/test_app.py     # smoke test

Open `/live?big=1` for the big-screen view (no nav bar).

## What the Pi sends

Every request needs the shared token from `.env`: `Authorization: Bearer <INGEST_TOKEN>`.

### `POST /api/round` - after every round (this is what gets saved)

    {
      "session": "pi-20261111-153000",   required. The Pi's own name for the session; same for all its rounds
      "round": 1,                        required. 1, 2, 3...
      "card": "04A2B91C",                NFC card ID that was tapped. Leave out for a guest
      "score": 120, "thrown": 18, "landed": 14,
      "combos_called": 6, "combos_hit": 2, "best_streak": 3,     optional
      "hr": {"bpm": 132, "good": true},                          optional. Rest reading after this round
      "duration_s": 30, "unmatched_impacts": 1,                  optional. Round length; bag hits with no punch
      "started_at": 1794379269.0,                                optional. Session start, unix seconds
      "combos": [ ... ]                                          optional. The detail behind the totals, below
    }

`combos` is what the round dashboard is drawn from. It is the round as the hub saw it through the board messages in
`protocol/PROTOCOL.md`, one entry per combo the hub called:

    {"called": [1, 2, 3],            the combo sent to the bag            C,1-2-3
     "hit": true,                    the sound played after it            S,HIT / S,MISS
     "correct": 3, "points": 55,     how the hub scored it
     "punches": [                    what the wrists reported, in order
       {"hand": "L", "type": "J",    hand and punch type                  P,L,J,48213,11
        "peak_g": 11,                wrist peak acceleration
        "t": 1.42,                   seconds since the round started (the hub turns board time into this)
        "landed": true,              a bag impact matched the punch
        "impact": 72}]}              that impact's strength, 0-100        I,48260,72

| Protocol message | Where it shows on the round dashboard |
|---|---|
| `P` punch (hand, type, peak g, time) | "Punches by type", the punch timeline, each punch in the combo table, average and hardest punch |
| `I` bag impact (time, strength) | Landed or missed for every punch, average bag impact, "bag hits with no punch" |
| `H` heart rate (BPM, good or not) | Rest heart rate for the round |
| `C` combo | The "Called" column of the combo table |
| `S` sound `HIT` / `MISS` | The "Result" column of the combo table, combos completed |
| `M` mode `ROUND` / `REST` | One upload per round, taken after its rest; round length |
| `T` clock ping, `R` raw samples | Not uploaded. `T` is how the hub lines up board times before it sends `t`; `R` is training only |

- Answers `{"ok": true, "session_ids": [12]}`, or `400` with `{"error": "..."}` saying what is wrong.
- A list of rounds in one request is accepted too.
- `thrown`, `landed`, `combos_called` and `combos_hit` are counted from `combos` when left out.
- A round sent without `combos` is still saved; its page then shows the totals only.
- Sending the same `session` + `round` again **replaces** it, so the Pi can retry after a failed upload without
  double counting. The Pi should keep rounds it could not send and retry them.
- The card decides whose round it is: it is matched to the athlete account that has that card ID. Rounds for a card
  nobody has linked yet are kept, and show up as soon as an athlete links that card.

The Pi does not need to build these requests by hand: `../backend/pi_uploader.py` does it, including the retries.

That is the only thing the Pi sends. There is no in-round live feed: the "Latest round" page (`/live`) shows the
most recent round uploaded for the viewer's gym and reloads itself within a few seconds of a newer one arriving.

## Logins and gyms

Every page needs a login. The login page has two options, **Athlete** and **Coach**, and everything is separated by gym.

| Role | Sees |
|---|---|
| Coach | The athletes of their own gym: leaderboard, each athlete's page, their sessions |
| Athlete | Their own stats ("My stats") and the same pages for the other athletes of their gym |

Nobody sees athletes or sessions of another gym (those pages answer "not found"). Guest rounds and rounds for a card
with no account belong to no gym, so they appear on no page.

- **Coach sign-up** creates a new gym and shows its gym code on the Athletes page.
- **Athlete sign-up** needs that gym code. The NFC card ID can be typed at sign-up or linked later from "My stats".
  One card belongs to one account.
- An athlete can delete their own account from "My stats"; that also deletes their sessions.
- Passwords are stored hashed.

Demo logins for `--sim` (password `demo1234` for all):

| Gym (code) | Coach | Athletes |
|---|---|---|
| Southpaw Gym (`SOUTH1`) | `coach` | `kai`, `mei` |
| Northside Boxing (`NORTH1`) | `coach2` | `arjun`, `sam` |

## Pages

| URL | What |
|---|---|
| `/login`, `/signup` | Coach or athlete login; sign-up creates a gym (coach) or joins one with its code (athlete) |
| `/live` (`?big=1`) | The round dashboard for the gym's most recent round; reloads itself when a newer round arrives |
| `/` | The gym's athletes: high score, accuracy, sessions |
| `/player/<player id>` | An athlete's page: high score, best streak, accuracy trend, score, rest heart rate over time, history, card |
| `/sessions`, `/session/<id>` | The gym's history and per-session rounds + heart-rate chart |
| `/session/<id>/round/<n>` | Round dashboard: totals, punch power, punches by type, every punch on a timeline, each combo called and what was thrown |

A player ID is the athlete's username in upper case (`kai` -> `/player/KAI`).

## Code map

| File | What it does |
|---|---|
| `app.py` | Routes, login, the Pi's upload endpoint |
| `db.py` | Tables (gyms, users, sessions, rounds, combos, punches) and the database connection |
| `accounts.py` | Sign-up, login, gyms, card linking |
| `store.py` | Saving uploaded rounds with their combos and punches, and every query the pages use (always limited to one gym) |
| `sim.py` | Fake Pi for `--sim` |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | The Docker setup |

## Decisions to confirm with the team

1. **The Pi uploads through `/api/round`, not straight into Postgres.** The database port is not opened to the network;
   the website checks the token and the data, then writes it. C needs to call `../backend/pi_uploader.py` from the hub.
2. **Round payload.** The hub now sends every combo and punch of the round (`combos`). The camera's guard tracking is
   not uploaded or shown yet.
3. **Heart-rate recovery metric.** One rest BPM per round, no peak to measure a drop from. The dashboard plots rest BPM.
4. **No in-round view.** The big screen shows a round only after it ends. The called combo, timer and punch-by-punch
   feed are gone from the website; during a round the bag board's own screen is the display.
5. **Card IDs are typed in.** An athlete has to know their card's ID to link it. Print it on the card, or show it on the Pi.
6. **Hosting.** The site is served over plain HTTP on port 5000 by Flask's built-in server. Put it behind HTTPS
   before using real passwords over the internet.
