# NFC card reader (Member D)

The player taps their NFC card on a PN532 reader at the start of a game. The reader
process sends the card ID to two places on the Pi:

| Port (127.0.0.1) | Who | What it does with `{"uid": "F6D94A06"}` |
|---|---|---|
| 5006 | hub | this card is the player for the next game (no tap = guest) |
| 5007 | Telegram bot | links the card to a chat after `/link`; otherwise says "card scanned" to the card's owner |

- **One tap per card:** a card held on the reader counts as **one** tap. The same card counts again only after
  it has been away from the reader for 2 s (`--gap`). A different card counts straight away.
- **Card IDs:** upper-case hex with no spaces. 4-byte cards give 8 characters (`F6D94A06`); 7-byte cards give 14.
  The hub, the bot and the website all accept both.
- **Everything is optional:** the hub and the bot don't need to be running for the reader to work, and the game
  works without the reader.
- **Errors:** if the reader is unplugged or stops answering (an I²C error), the program says so, waits 3 s,
  opens the reader again and carries on. It doesn't crash.

| File | What |
|---|---|
| `nfc_reader.py` | The program you run: reads cards, sends taps |
| `nfc_core.py` | Card ID → text, the "is this a new tap?" filter, the UDP sender (no hardware needed) |
| `test_nfc.py` | Tests with a fake reader, no hardware needed: `python test_nfc.py` |

## Try it without the reader (any computer, also Windows)

```powershell
cd nfc
python test_nfc.py                      # all tests pass
python nfc_reader.py --fake --dry-run   # type a card ID + Enter to "tap"; only printed
python nfc_reader.py --fake             # the same, but the taps are sent to the hub + bot
```

- **Enter on its own** "taps" the test card `04A1B2C3`.
- **With the hub or bot running:** a fake tap reaches the hub (`python -m southpaw play boards.toml`) or the bot
  (`python bot.py`) on the same computer exactly like a real one.

## Wiring the PN532 to the Pi (I²C)

Tested with the Elechouse PN532 NFC Module V3 on a Raspberry Pi 4.

1. **Header pins:** solder the 4-pin header (GND, VCC, SDA, SCL). Pins that are only pushed in make a flickery
   connection.
2. **Mode switches:** I²C = **switch 1 ON, switch 2 OFF**. Only change the switches with the power off.
3. **Wires** (female-to-female jumpers) on the Pi's GPIO header:

| PN532 | Pi pin | |
|---|---|---|
| VCC | **pin 1** (3.3 V) | **not 5 V**: the Pi's I²C pins are 3.3 V only |
| SDA | pin 3 (GPIO2, SDA) | |
| SCL | pin 5 (GPIO3, SCL) | |
| GND | pin 9 (GND) | |

- **Pin 7 stays empty.**
- **A case fan** on pins 4 (5 V) and 6 (GND) fits next to the reader's wires.

## Setting it up on the Pi

```bash
# 1. Turn on I2C (once): Interface Options -> I2C -> Yes, then reboot
sudo raspi-config
sudo reboot

# 2. Check the Pi can see the reader: a "24" should appear in the grid
sudo apt install -y i2c-tools
i2cdetect -y 1

# 3. Install the libraries in a venv (the system Python 3.13 is fine here; run from the repo folder)
cd nfc
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Test the reader on its own: tap a card, its ID is printed, nothing is sent
.venv/bin/python nfc_reader.py --dry-run

# 5. The real thing: every tap goes to the hub + bot
.venv/bin/python nfc_reader.py
```

You should see `PN532 found on I2C bus 1 (firmware 1.6)`, `Ready: tap a card`, and one
`Tap: card F6D94A06 -> sent to hub + bot` line per tap.

### Options

| Option | What it does |
|---|---|
| `--dry-run` | Only prints the card IDs; sends nothing (to test the reader on its own) |
| `--quiet` | Only prints problems, not every tap (for running in the background) |
| `--bus 6` | The reader is on another I²C bus (default `1`), see below |
| `--gap 3` | The same card must be away 3 s before it counts again (default 2) |
| `--hub-port`, `--bot-port` | Other UDP ports (default 5006 and 5007) |
| `--host` | Send to another computer instead of this Pi (default `127.0.0.1`) |
| `--fake` | No reader: type card IDs on the keyboard |

### The reader on another I²C bus (optional)

The Pi 4 can turn on extra I²C buses, for example if pins 3 and 5 are needed for something else.
**Bus 6** uses **SDA = pin 15 (GPIO22)** and **SCL = pin 16 (GPIO23)**. VCC and GND stay the same.

```bash
# add this line at the end of the Pi's config, then reboot
echo "dtoverlay=i2c6" | sudo tee -a /boot/firmware/config.txt
sudo reboot
i2cdetect -y 6                                # "24" should appear
.venv/bin/python nfc_reader.py --bus 6
```

## If it doesn't work

| Problem | Try |
|---|---|
| `i2cdetect` shows no `24` | Check the 4 wires and the switches (I²C mode), with the power off before changing them. Are the header pins soldered? |
| `Can't reach the PN532 (...)` repeating | Same as above. Also: is I²C turned on (`ls /dev/i2c-1`)? Is your user in the `i2c` group (`groups`; if not: `sudo adduser $USER i2c`, then log out and in)? `ModuleNotFoundError` means step 3 (install) was skipped or you're not using `.venv/bin/python`. |
| Works, then `Reader stopped answering` now and then | The Pi's I²C and the PN532 sometimes disagree on timing. Slow I²C down: add `dtparam=i2c_arm_baudrate=50000` to `/boot/firmware/config.txt` and reboot. If it's still flaky, use SPI mode instead (Adafruit `PN532_SPI`). |
| Tap shows here, but the bot doesn't react | Is the bot running on the **same** Pi (`--host` otherwise)? Did you send `/link` in Telegram first (it waits 60 s for the tap)? Is this program running without `--dry-run`? |
| Tap shows here, but the hub plays as guest | The hub only listens for taps when it runs with the real boards (`play boards.toml`); `play --sim` uses `--card` instead. Tap while the bag shows the start screen, before the 5 start hits. |

## Credits

- **The PN532 code:** based on Adafruit's `pn532_simpletest.py` example and the Adafruit CircuitPython PN532 library (MIT).
- **Other I²C buses:** opened with Adafruit's `adafruit-extended-bus` (MIT).

See `docs/CITATIONS.md`.
