"""
nfc_reader.py - Southpaw OS, NFC card reader (Member D)

The player taps their NFC card on the PN532 reader next to the bag. This process:

  PN532 (I2C on the Pi)  ->  card ID  ->  new tap?  ->  {"uid": ...}  ->  hub (5006) + Telegram bot (5007)

  * asks the reader for a card about 5 times a second
  * a card held on the reader counts as ONE tap (see TapFilter in nfc_core.py)
  * if the reader isn't plugged in or stops answering (an I2C error), it says why, waits a
    few seconds, opens the reader again and carries on; it never crashes because of the
    reader. The game works without it (no card = guest)

Examples (in the nfc folder, inside the venv):
  python nfc_reader.py              # the real PN532 on the Pi (I2C bus 1 = pins 3 and 5)
  python nfc_reader.py --dry-run    # only print the card IDs, send nothing (to test the reader)
  python nfc_reader.py --bus 6      # the reader is on another I2C bus (e.g. bus 6 = pins 15 and 16)
  python nfc_reader.py --quiet      # only print problems, not every tap (for running as a service)
  python nfc_reader.py --fake       # no reader: type a card ID (or just press Enter) to "tap"; works on Windows
  python nfc_reader.py --gap 3      # the same card must be away 3 s before it counts again

Wiring and setup: see README.md in this folder.

Credits: the PN532 code follows Adafruit's example `pn532_simpletest.py` from the
Adafruit_CircuitPython_PN532 library (MIT licence); other I2C buses use Adafruit's
adafruit-extended-bus (MIT). See docs/CITATIONS.md.
"""

import argparse
import logging
import time

from nfc_core import BOT_PORT, HUB_PORT, TapFilter, TapSender, format_uid

log = logging.getLogger("nfc")

POLL_TIMEOUT_S = 0.2   # how long each "is there a card?" question waits (=> about 5 checks a second)
RETRY_S = 3.0          # wait this long before trying to reach a missing/broken reader again
FAKE_CARD = "04A1B2C3"  # what --fake "taps" when you just press Enter


# ------------------------------------------------------------ the real reader

def open_i2c(bus):
    """Open I2C bus number `bus` on the Pi.

    Bus 1 is the normal one (SDA = pin 3, SCL = pin 5) and is opened the way Adafruit's
    example does it. Any other bus (the Pi 4 can turn on extra ones, e.g. bus 6 on pins
    15 + 16, see README.md) is opened by its number with adafruit-extended-bus.

    The Adafruit libraries are imported here (not at the top) so that --fake and the tests
    run on a computer that doesn't have them, like Windows."""
    if bus == 1:
        import board                    # from adafruit-blinka: the Pi's pin names
        import busio
        return busio.I2C(board.SCL, board.SDA)   # GPIO3 = SCL (pin 5), GPIO2 = SDA (pin 3)
    from adafruit_extended_bus import ExtendedI2C
    return ExtendedI2C(bus)             # /dev/i2c-<bus>


def connect_pn532(bus=1):
    """Open the PN532 over I2C and get it ready to read cards. Raises if it isn't there."""
    from adafruit_pn532.i2c import PN532_I2C

    i2c = open_i2c(bus)
    try:
        pn532 = PN532_I2C(i2c, debug=False)            # I2C address 0x24 (i2cdetect shows "24")
        ic, ver, rev, support = pn532.firmware_version  # a quick "are you there?"; raises if not
        pn532.SAM_configuration()                      # normal mode: read cards held near it
    except Exception:
        _close(i2c)   # give the bus back before the next try, so it can be opened again
        raise
    log.info("PN532 found on I2C bus %d (firmware %d.%d)", bus, ver, rev)
    pn532.nfc_i2c = i2c   # keep the bus with the reader, so it can be closed on a reconnect
    return pn532


def _close(i2c):
    """Close an I2C bus, ignoring errors (it may already be broken; that's why we're closing it)."""
    try:
        i2c.deinit()
    except Exception:
        pass


def read_cards(connect, poll_timeout=POLL_TIMEOUT_S, retry_s=RETRY_S, sleep=time.sleep):
    """Yields the ID of every card the reader sees (as text, e.g. "F6D94A06"), forever.

    Yields None when there is no card, so the main loop keeps ticking.

    `connect()` opens the reader (connect_pn532 on the Pi; the tests pass a fake). When it
    fails, or the reader fails in the middle of a read, this logs why, waits `retry_s`
    seconds, and opens the reader again from scratch. Nothing here can stop the loop."""
    reader = None
    while True:
        if reader is None:
            try:
                reader = connect()
                log.info("Ready: tap a card")
            except Exception as e:   # not wired / I2C off / library missing: say why, retry later
                log.warning("Can't reach the PN532 (%s: %s). Trying again in %.0f s...",
                            type(e).__name__, e, retry_s)
                sleep(retry_s)
                continue
        try:
            raw = reader.read_passive_target(timeout=poll_timeout)   # None if no card
        except Exception as e:       # e.g. a loose wire mid-read (OSError / RuntimeError)
            log.warning("Reader stopped answering (%s: %s). Reconnecting in %.0f s...",
                        type(e).__name__, e, retry_s)
            if getattr(reader, "nfc_i2c", None) is not None:
                _close(reader.nfc_i2c)
            reader = None
            sleep(retry_s)
            continue
        yield format_uid(raw) if raw else None


# ------------------------------------------------------------ the fake reader (testing without hardware)

def read_cards_from_keyboard():
    """Type a card ID and press Enter to "tap" it; just Enter taps the test card 04A1B2C3."""
    print(f"Fake reader: type a card ID (hex, e.g. F6D94A06) and press Enter. "
          f"Just Enter = {FAKE_CARD}. Ctrl+C to stop.")
    while True:
        text = input("card> ").strip().replace(":", "").replace(" ", "")
        if not text:
            text = FAKE_CARD
        try:
            yield format_uid(bytes.fromhex(text))
        except ValueError:
            print("  not a hex card ID, try again (only 0-9 and A-F, an even number of them)")


# ------------------------------------------------------------ main loop

def run(cards, taps, sender=None):
    """Goes through the card reads, and for every NEW tap sends it (or, with no sender, i.e.
    --dry-run, just prints it). Returns how many taps there were (the tests use this).
    Stops when `cards` runs out (only the tests' cards do; the real reader never stops)."""
    count = 0
    for uid in cards:
        if uid is None or not taps.is_new_tap(uid):
            continue                      # no card, or the same card still on the reader
        count += 1
        if sender is None:
            print(f"Tap: card {uid}  (dry run: not sent)", flush=True)
        else:
            sender.send_tap(uid)
            log.info("Tap: card %s -> sent to hub + bot", uid)
    return count


def main():
    parser = argparse.ArgumentParser(description="Southpaw OS NFC card reader")
    parser.add_argument("--bus", type=int, default=1,
                        help="I2C bus number the reader is on (default 1 = pins 3 + 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only print the card IDs; don't send anything to the hub or bot")
    parser.add_argument("--quiet", action="store_true",
                        help="only print problems (not every tap)")
    parser.add_argument("--fake", action="store_true", help="no reader: type card IDs instead")
    parser.add_argument("--gap", type=float, default=2.0,
                        help="seconds the same card must be away before it counts again (default 2)")
    parser.add_argument("--host", default="127.0.0.1", help="where the hub and bot run (default this Pi)")
    parser.add_argument("--hub-port", type=int, default=HUB_PORT, help=f"hub's UDP port (default {HUB_PORT})")
    parser.add_argument("--bot-port", type=int, default=BOT_PORT,
                        help=f"Telegram bot's UDP port (default {BOT_PORT})")
    args = parser.parse_args()

    # --quiet: only warnings (reader problems) are printed; --dry-run still prints each card
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    sender = None if args.dry_run else TapSender(args.host, (args.hub_port, args.bot_port))
    # The fake reader is one tap per Enter, so every entry counts (gap 0).
    taps = TapFilter(gap_s=0 if args.fake else args.gap)
    cards = read_cards_from_keyboard() if args.fake else read_cards(lambda: connect_pn532(args.bus))
    if sender:
        log.info("Sending taps to %s ports %d (hub) and %d (bot)", args.host, args.hub_port, args.bot_port)
    else:
        log.info("Dry run: card IDs are printed, nothing is sent")

    try:
        run(cards, taps, sender)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if sender:
            sender.close()
            log.info("Stopped (%d messages sent, %d send errors)", sender.sent, sender.errors)


if __name__ == "__main__":
    main()
