# NFC card reader (Member D)

The player taps their NFC card on a PN532 reader at the start of a game. The reader
process sends the card ID to two places on the Pi:

| Port (127.0.0.1) | Who | What it does with `{"uid": "04A2B91C"}` |
|---|---|---|
| 5006 | hub | this card is the player for the next game (no tap = guest) |
| 5007 | Telegram bot | links the card to a chat after `/link`; otherwise says "card scanned" to the card's owner |

A card held on the reader counts as **one** tap. The same card counts again only after it
has been away from the reader for 2 s (`--gap`). The hub and the bot don't need to be running
for the reader to work, and the game works without the reader.

| File | What |
|---|---|
| `nfc_reader.py` | The process you run: reads cards, sends taps |
| `nfc_core.py` | Card ID → text, the "is this a new tap?" filter, the UDP sender (no hardware needed) |
| `test_nfc.py` | Tests for `nfc_core.py`: `python test_nfc.py` |

## Try it without the reader (any computer, also Windows)

```powershell
cd nfc
python test_nfc.py            # all tests pass
python nfc_reader.py --fake   # type a card ID + Enter to "tap" (just Enter = 04A1B2C3)
```

With the hub (`python -m southpaw play ...`) or the bot (`python bot.py`) running on the same
computer, a fake tap reaches them exactly like a real one.

## Wiring the PN532 to the Pi (I²C)

1. **Header pins:** solder the 4-pin header on the I²C side of the board (VCC, GND, SDA, SCL).
2. **Mode switches:** I²C = **switch 1 ON, switch 2 OFF**. (Change the switches only with the power off.)
3. **Wires** (female-to-female jumpers), Pi GPIO header:

| PN532 | Pi pin | |
|---|---|---|
| VCC | **pin 1** (3.3 V) | **not 5 V**: the Pi's I²C pins are 3.3 V only |
| GND | pin 9 (GND) | |
| SDA | pin 3 (GPIO2, SDA) | |
| SCL | pin 5 (GPIO3, SCL) | |

## Setting it up on the Pi

```bash
# 1. Turn on I2C (once): Interface Options -> I2C -> Yes, then reboot
sudo raspi-config
sudo reboot

# 2. Check the Pi can see the reader: a "24" should appear in the grid
sudo apt install -y i2c-tools
i2cdetect -y 1

# 3. Install the library in a venv (the system Python 3.13 is fine here)
cd ~/Comp-Arch-SouthpawOS/nfc
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Run it, tap a card
.venv/bin/python nfc_reader.py
```

You should see `PN532 found (firmware 1.6)`, `Ready: tap a card`, and one
`Tap: card 04...` line per tap.

## If it doesn't work

| Problem | Try |
|---|---|
| `i2cdetect` shows no `24` | Check the 4 wires and the switches (I²C mode), power off before changing them. Are the header pins soldered? Pins only pushed in make a flickery connection. |
| `Can't reach the PN532 (...)` repeating | Same as above. Also: is I²C turned on (`ls /dev/i2c-1`)? Is your user in the `i2c` group (`groups`)? |
| Works, then `Reader stopped answering` now and then | The Pi's I²C and the PN532 sometimes disagree on timing. Slow I²C down: add `dtparam=i2c_arm_baudrate=50000` to `/boot/firmware/config.txt` and reboot. If it's still flaky, use SPI mode (Adafruit `PN532_SPI`). |
| Tap shows here, but the bot doesn't react | Is the bot running on the **same** Pi? Did you send `/link` in Telegram first (it waits 60 s for the tap)? |

## Credits

Based on Adafruit's `pn532_simpletest.py` example and the Adafruit CircuitPython PN532 library
(MIT), see `docs/CITATIONS.md`.
