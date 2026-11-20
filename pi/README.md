# Pi setup: everything starts by itself (Member D)

Switch the Pi on and the whole game is ready, without logging in or typing anything.
**Four programs run in the background as systemd services**, all from **one account** and one copy of the repo:

| Service | Program | Folder | Talks to |
|---|---|---|---|
| `southpaw-hub` | the game: boards over Bluetooth, rounds, scores | `hub/` | boards (BLE), listens on UDP 5005 + 5006, sends to 5007 |
| `southpaw-bot` | Telegram bot | `telegram_bot/` | Telegram (internet), listens on UDP 5007 |
| `southpaw-nfc` | NFC card reader | `nfc/` | sends each tap to UDP 5006 + 5007 |
| `southpaw-vision` | webcam guard tracking | `vision/` | sends to UDP 5005 |

- **Order doesn't matter:** they only talk over local UDP, so each one can start, stop or crash on its own.
- **Restarts:** systemd restarts a program 5–10 s after it stops.
- **Everything is optional:** the game runs without the bot, the reader or the camera, as before.

**The game after boot:** wait about 30 s, switch on the 3 boards, and the bag shows the start screen.
Then tap your card and hit the bag 5 times. The hub plays one game after another, forever.

## One-time setup

All commands are on the Pi, in the account that will run everything (we use `vision`), from the repo folder
(here `~/southpaw`).

### 1. The code
The repo is private, so the code is copied from a desktop with the repo on it (Windows PowerShell):

```powershell
cd <the repo folder>
# autocrlf=false keeps Linux line endings; -o + scp because a PowerShell pipe damages the archive
git -c core.autocrlf=false archive -o "$env:TEMP\southpaw.tar" HEAD
scp "$env:TEMP\southpaw.tar" vision@southpaw.local:
```
```bash
# Pi: unpack over ~/southpaw. Your .venv folders, .env, boards.toml and databases stay as they are.
mkdir -p ~/southpaw && tar -xf ~/southpaw.tar -C ~/southpaw && rm ~/southpaw.tar
```

### 2. Each program once by hand
Each part needs its own venv. See each folder's README; in short:

```bash
cd ~/southpaw
# hub
python3 -m venv hub/.venv && hub/.venv/bin/pip install -r hub/requirements.txt
cp -n hub/boards.example.toml hub/boards.toml
# Telegram bot: plus telegram_bot/.env with the bot token (telegram_bot/README.md)
python3 -m venv telegram_bot/.venv && telegram_bot/.venv/bin/pip install -r telegram_bot/requirements.txt
# NFC reader (I2C turned on, see nfc/README.md)
python3 -m venv nfc/.venv && nfc/.venv/bin/pip install -r nfc/requirements.txt
# vision: needs Python 3.12 through uv (vision/README.md)
cd vision && uv venv --python 3.12 .venv && uv pip install -r requirements.txt && cd ..
```

**Check that this account can reach every piece of hardware:**

```bash
groups                                         # should include video and i2c
(cd hub && .venv/bin/python -m southpaw scan)  # lists P1D-WRIST-L, P1D-WRIST-R and P1D-BAG
```

If the scan says it isn't allowed to use Bluetooth: `sudo adduser $USER bluetooth`, log out and back in, and scan
again.

### 3. Switch on auto-start
```bash
bash pi/install.sh
```
It installs and starts the 4 services and prints whether each one is `active`. If a part isn't set up yet
(no `.venv`, no `boards.toml`, no bot token), it says so and skips that one. Set it up and run the script again.

## Settings: `pi/southpaw.env`
`install.sh` makes it the first time. It's git-ignored because it can hold the website token.

| Setting | For | Example |
|---|---|---|
| `HUB_ARGS` | the game's options | `--rounds 1 --round-secs 30 --rest-secs 15` (default: 3 rounds of 30 s, 20 s rest) |
| `SOUTHPAW_SERVER`, `INGEST_TOKEN` | upload every round to Jovis's website | empty = no upload |
| `NFC_ARGS` | the reader's options | `--bus 6` |
| `VISION_ARGS` | vision's options | `--source 1` |
| `BT_FIRST_SECS` | at boot, Wi-Fi stays off until the boards connect, at most this long (see below) | `180` (default), `0` = off |

After changing it: `sudo systemctl restart southpaw-hub` (or the service you changed).

## Wi-Fi off while the boards connect (`southpaw-btfirst`)

On a Pi 4, **Wi-Fi and Bluetooth share one chip and one antenna**. With the Pi's Wi-Fi on **2.4 GHz** (for example a
phone hotspot with "Maximize Compatibility" on) in a room full of Bluetooth devices, the boards can't finish
connecting: the hub log fills with `bag disconnected` / `wristL disconnected`, and `sudo btmon` shows
`Connection Failed to be Established (0x3e)`. At home with few devices around it works; in a busy room it doesn't.
Once a board is connected it usually stays connected.

So at every boot `southpaw-btfirst` (`pi/bluetooth_first.sh`) keeps **Wi-Fi off until the hub says
`all boards connected`** (at most `BT_FIRST_SECS`, default 3 minutes), then switches it back on. Wi-Fi always comes
back on, also if something goes wrong.

- **Switch the 3 boards on within the first ~3 minutes** after switching the Pi on.
- While Wi-Fi is off: no SSH over Wi-Fi, the bot can't reach Telegram, nothing is uploaded. It catches up after.
- **A board dropped later and won't come back?** Switch the boards off, run this, then switch them on straight away
  (SSH freezes until they're connected):
  ```bash
  sudo bash ~/southpaw/pi/bluetooth_first.sh --restart-hub
  ```
- On a **5 GHz** network (or Ethernet) this isn't needed: set `BT_FIRST_SECS=0`. The Pi only uses 5 GHz when its Wi-Fi
  country is set: `sudo raspi-config nonint get_wifi_country` should say `SG`
  (`sudo raspi-config nonint do_wifi_country SG`, then reboot).

## Day to day

| What | Command |
|---|---|
| Are they running? | `systemctl status 'southpaw-*'` (q to quit) |
| Watch the game live | `journalctl -u southpaw-hub -f` (Ctrl+C stops watching, not the game) |
| Watch everything | `journalctl -u 'southpaw-*' -f` |
| Restart one / all | `sudo systemctl restart southpaw-hub` / `sudo systemctl restart 'southpaw-*'` |
| Stop one until the next boot | `sudo systemctl stop southpaw-vision` |
| After copying new code (step 1) | `sudo systemctl restart 'southpaw-*'` |
| Turn auto-start off again | `bash pi/install.sh --remove` |

**Running a program by hand (to test or debug):** stop its service first. Two hubs can't share the boards, and
two bots with the same token make Telegram answer "Conflict":

```bash
sudo systemctl stop southpaw-hub
cd ~/southpaw/hub && .venv/bin/python -m southpaw play boards.toml --games 1 --rounds 1
sudo systemctl start southpaw-hub     # back to automatic
```
This also applies to running the hub from another account on the Pi.

## Backing up the settings and data

Some files on the Pi aren't in git and can't be rebuilt from it. `pi/backup.sh` packs them into one file:

| File | Holds |
|---|---|
| `telegram_bot/.env` | the bot token, `ADMIN_CHAT_IDS` |
| `telegram_bot/players.db` | which card belongs to which Telegram chat, round history |
| `pi/southpaw.env` | game settings, the website address + token |
| `hub/southpaw.db` | scores, sessions, leaderboard |
| `hub/boards.toml` | the board addresses |
| `hub/logs/unsent_rounds.jsonl` | rounds still waiting for the website (if any) |
| `/etc/NetworkManager/system-connections/` | the saved Wi-Fi networks and their passwords (asks for sudo) |

```bash
bash pi/backup.sh          # makes ~/southpaw-backup-<date>-<time>.tar.gz (outside the repo folder)
```
Copy it to your computer. The script prints the exact command, e.g. in PowerShell:
```powershell
scp vision@southpaw.local:southpaw-backup-20261120-1830.tar.gz .
```

**To put it back** (a new SD card, or something got broken): set the Pi up as in steps 1–3, copy the file back with
`scp southpaw-backup-....tar.gz vision@southpaw.local:`, then run:
```bash
bash pi/backup.sh --restore ~/southpaw-backup-20261120-1830.tar.gz    # stops the services, restores, starts them
```
- **Keep backups private:** a backup holds the bot token, the website token and Wi-Fi passwords. Only your account can
  read it on the Pi, and it's never inside the repo folder, so it can't be committed by accident.
- **The databases** are copied with SQLite's own backup function, so the copy is complete even while the game runs.

## If something doesn't work

| Problem | Look at |
|---|---|
| A service shows `failed` or `activating (auto-restart)` | `journalctl -u <service> -n 50`: the last 50 lines say why |
| Boards connect and drop again and again (`... disconnected` many times a second) | The Pi's 2.4 GHz Wi-Fi, see "Wi-Fi off while the boards connect" above |
| A board's screen says connected, but the hub says `not found` | A half-open link: `bluetoothctl devices Connected` lists it. `sudo systemctl restart southpaw-hub` (restarts Bluetooth too) |
| The hub keeps waiting for a board | Is the board switched on? Is it already connected somewhere else (a phone, a laptop, a second hub)? `sudo systemctl restart bluetooth southpaw-hub` |
| The bot doesn't answer | Is it running on another computer too (Conflict)? Is the Pi online? |
| Vision says the camera can't be opened | Plugged in? Is `video` in `groups`? Is another vision process running? |

## Credits
Plain systemd unit files and bash scripts; no borrowed code. `backup.sh` uses Python's built-in `sqlite3`
backup function (Python standard library).
