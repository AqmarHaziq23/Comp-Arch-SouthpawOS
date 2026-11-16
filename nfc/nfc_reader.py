"""
nfc_reader.py - Southpaw OS, NFC card reader (Member D)

The player taps their NFC card on the PN532 reader next to the bag. This process:

  PN532 (I2C on the Pi)  ->  card ID  ->  new tap?  ->  {"uid": ...}  ->  hub (5006) + Telegram bot (5007)

  * asks the reader for a card about 5 times a second
  * a card held on the reader counts as ONE tap (see TapFilter in nfc_core.py)
  * if the reader isn't plugged in or stops answering, it keeps retrying every few
    seconds instead of crashing; the game works without it (no card = guest)

Examples (in the nfc folder, inside the venv):
  python nfc_reader.py              # the real PN532 on the Pi
  python nfc_reader.py --fake       # no reader: type a card ID (or just press Enter) to "tap"; works on Windows
  python nfc_reader.py --gap 3      # the same card must be away 3 s before it counts again

Wiring and setup: see README.md in this folder.

Credits: the PN532 code follows Adafruit's example `pn532_simpletest.py` from the
Adafruit_CircuitPython_PN532 library (MIT licence), see docs/CITATIONS.md.
"""

import argparse
import logging
import time

from nfc_core import BOT_PORT, HUB_PORT, TapFilter, TapSender, format_uid

log = logging.getLogger("nfc")

POLL_TIMEOUT_S = 0.2   # how long each "is there a card?" question waits (=> about 5 checks a second)
RETRY_S = 3.0          # wait this long before trying to reach a missing/broken reader again


# ------------------------------------------------------------ the real reader

def connect_pn532():
    """Open the PN532 over I2C and get it ready to read cards. Raises if it isn't there.

    The Adafruit libraries are imported here (not at the top) so that --fake runs on a
    computer that doesn't have them, like Windows."""
    import board                                  # from adafruit-blinka: the Pi's pin names
    import busio
    from adafruit_pn532.i2c import PN532_I2C

    i2c = busio.I2C(board.SCL, board.SDA)         # GPIO3 = SCL (pin 5), GPIO2 = SDA (pin 3)
    pn532 = PN532_I2C(i2c, debug=False)           # I2C address 0x24 (i2cdetect shows "24")
    ic, ver, rev, support = pn532.firmware_version  # a quick "are you there?"; raises if not
    log.info("PN532 found (firmware %d.%d)", ver, rev)
    pn532.SAM_configuration()                     # normal mode: read cards held near it
    return pn532


def read_cards_from_pn532():
    """Yields the ID of every card the reader sees (as text), forever.

    Yields None when there is no card, so the main loop keeps ticking."""
    pn532 = None
    while True:
        if pn532 is None:
            try:
                pn532 = connect_pn532()
                log.info("Ready: tap a card")
            except Exception as e:   # not wired / I2C off / library missing: say why, retry later
                log.warning("Can't reach the PN532 (%s). Trying again in %.0f s...", e, RETRY_S)
                time.sleep(RETRY_S)
                continue
        try:
            raw = pn532.read_passive_target(timeout=POLL_TIMEOUT_S)   # None if no card
        except Exception as e:       # a loose wire mid-read: reconnect from scratch
            log.warning("Reader stopped answering (%s). Reconnecting...", e)
            pn532 = None
            time.sleep(RETRY_S)
            continue
        yield format_uid(raw) if raw else None


# ------------------------------------------------------------ the fake reader (testing without hardware)

def read_cards_from_keyboard():
    """Type a card ID and press Enter to "tap" it; just Enter taps the test card 04A1B2C3."""
    print("Fake reader: type a card ID (hex, e.g. 04A2B91C) and press Enter. "
          "Just Enter = 04A1B2C3. Ctrl+C to stop.")
    while True:
        text = input("card> ").strip().replace(":", "").replace(" ", "")
        if not text:
            text = "04A1B2C3"
        try:
            yield format_uid(bytes.fromhex(text))
        except ValueError:
            print("  not a hex card ID, try again (only 0-9 and A-F)")


# ------------------------------------------------------------ main loop

def main():
    parser = argparse.ArgumentParser(description="Southpaw OS NFC card reader")
    parser.add_argument("--fake", action="store_true", help="no reader: type card IDs instead")
    parser.add_argument("--gap", type=float, default=2.0,
                        help="seconds the same card must be away before it counts again (default 2)")
    parser.add_argument("--host", default="127.0.0.1", help="where the hub and bot run (default this Pi)")
    parser.add_argument("--hub-port", type=int, default=HUB_PORT)
    parser.add_argument("--bot-port", type=int, default=BOT_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    sender = TapSender(args.host, (args.hub_port, args.bot_port))
    # The fake reader is one tap per Enter, so every entry counts (gap 0).
    taps = TapFilter(gap_s=0 if args.fake else args.gap)
    cards = read_cards_from_keyboard() if args.fake else read_cards_from_pn532()
    log.info("Sending taps to %s ports %d (hub) and %d (bot)", args.host, args.hub_port, args.bot_port)

    try:
        for uid in cards:
            if uid is not None and taps.is_new_tap(uid):
                sender.send_tap(uid)
                log.info("Tap: card %s -> sent to hub + bot", uid)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        sender.close()
        log.info("Stopped (%d messages sent, %d send errors)", sender.sent, sender.errors)


if __name__ == "__main__":
    main()
