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
      "started_at": 1794379269.0                                 optional. Session start, unix seconds
    }

- Answers `{"ok": true, "session_ids": [12]}`, or `400` with `{"error": "..."}` saying what is wrong.
- A list of rounds in one request is accepted too.
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
| `/live` (`?big=1`) | Latest round for the gym, big-screen style: athlete, round score, thrown/landed, accuracy, combos, rest heart rate, session total |
| `/` | The gym's athletes: high score, accuracy, sessions |
| `/player/<player id>` | An athlete's page: high score, best streak, accuracy trend, score, rest heart rate over time, history, card |
| `/sessions`, `/session/<id>` | The gym's history and per-session rounds + heart-rate chart |

A player ID is the athlete's username in upper case (`kai` -> `/player/KAI`).

## Code map

| File | What it does |
|---|---|
| `app.py` | Routes, login, the Pi's upload endpoint |
| `db.py` | Tables (gyms, users, sessions, rounds) and the database connection |
| `accounts.py` | Sign-up, login, gyms, card linking |
| `store.py` | Saving uploaded rounds, and every query the pages use (always limited to one gym) |
| `sim.py` | Fake Pi for `--sim` |
| `Dockerfile`, `docker-compose.yml`, `.env.example` | The Docker setup |

## Decisions to confirm with the team

1. **The Pi uploads through `/api/round`, not straight into Postgres.** The database port is not opened to the network;
   the website checks the token and the data, then writes it. C needs to call `../backend/pi_uploader.py` from the hub.
2. **Round payload.** The fields above are a proposal. `score`, `thrown` and `landed` per round are what the pages need;
   the hub's per-punch detail is not uploaded.
3. **Heart-rate recovery metric.** One rest BPM per round, no peak to measure a drop from. The dashboard plots rest BPM.
4. **No in-round view.** The big screen shows a round only after it ends. The called combo, timer and punch-by-punch
   feed are gone from the website; during a round the bag board's own screen is the display.
5. **Card IDs are typed in.** An athlete has to know their card's ID to link it. Print it on the card, or show it on the Pi.
6. **Hosting.** The site is served over plain HTTP on port 5000 by Flask's built-in server. Put it behind HTTPS
   before using real passwords over the internet.
