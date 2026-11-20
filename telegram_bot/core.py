"""
core.py - Southpaw OS Telegram bot

The bot's logic, kept separate from the Telegram plumbing (bot.py) so it can be
tested without a real Telegram bot. It handles:

  * LINKING a player's NFC card to their Telegram chat:
      player sends /link  ->  "tap your card within 60 s"  ->  card tapped on the Pi
      ->  card UID saved against their chat  ->  "Card linked!"
    Only one link can be waiting at a time (there is one card reader). If a second
    player sends /link, they take over and the first is told to try again.

  * Messages from other programs on the Pi (UDP, see bot.py):
      {"uid": "04A1B2C3"}                                      NFC reader: a card was tapped
      {"kind": "card",  "uid": "04A1B2C3"}                     (same thing, older form; simulate.py)
      {"kind": "round", "uid": "04A1B2C3", "round": {...}}     hub: a round finished, send the summary

  * /camera: a photo from vision with a short "can the camera see you?" check
    (camera_check.py). Only for chats with a linked card: linking needs a tap on the real
    reader, so only people at the bag can see pictures of the room. The "card scanned"
    message also gets a one-line camera check while vision is running.

  * /status and /shutdown for the Pi's owner only (ADMIN_CHAT_IDS in .env, see pi_admin.py).

Why a Telegram bot can't just use @usernames: bots may only message people who have
opened the bot and pressed Start, and they do it by numeric chat id. That's why each
player links their card once with /link.
"""

import html
import logging
import time

import camera_check
from formatting import format_round
from storage import normalise_uid

log = logging.getLogger("southpaw.core")


class SouthpawBot:
    def __init__(self, storage, send, link_timeout_s=60, clock=time.monotonic,
                 read_camera=camera_check.read_snapshot, wall_clock=time.time, admin_chat_ids=()):
        """
        storage:        a Storage (storage.py)
        send:           async function send(chat_id, html_text) that delivers a Telegram message
        read_camera:    function returning vision's latest (report, jpeg_bytes) (camera_check.py)
        wall_clock:     the time of day in seconds (vision's report says when it was made)
        admin_chat_ids: the Pi owner's chat ids (ADMIN_CHAT_IDS in .env): only they may /status, /shutdown
        """
        self.storage = storage
        self.send = send
        self.link_timeout_s = link_timeout_s
        self.clock = clock
        self.read_camera = read_camera
        self.wall_clock = wall_clock
        self.admin_chat_ids = set(admin_chat_ids)
        self.pending = None   # waiting /link: {"chat_id", "username", "first_name", "expires"}

    # ------------------------------------------------------------------------------------
    # Commands from players (called by bot.py). Each returns the reply text (Telegram HTML).
    # ------------------------------------------------------------------------------------

    def welcome(self, first_name):
        name = html.escape(first_name or "there")
        return (f"👊 Welcome to <b>Southpaw OS</b>, {name}!\n\n"
                "I'll send your boxing round results here.\n\n"
                "<b>To get started:</b> send /link, then tap your NFC player card on the Pi "
                f"within {self.link_timeout_s} seconds.\n\n"
                "Other commands:\n"
                "/me - which card is linked to you\n"
                "/last - your most recent round\n"
                "/camera - check the camera can see you (photo)\n"
                "/unlink - stop receiving results")

    async def start_link(self, chat_id, username, first_name):
        """Player sent /link: wait for the next card tap."""
        now = self.clock()
        # Someone else was already waiting? They lose their turn (one reader, one link at a time).
        if self.pending and self.pending["chat_id"] != chat_id and self.pending["expires"] > now:
            await self._safe_send(self.pending["chat_id"],
                                  "⚠️ Someone else started linking a card, so your link was cancelled. "
                                  "Send /link again when the reader is free.")
        self.pending = {"chat_id": chat_id, "username": username, "first_name": first_name,
                        "expires": now + self.link_timeout_s}
        return (f"📇 Tap your NFC card on the Pi within <b>{self.link_timeout_s} seconds</b> "
                "to link it to this chat.")

    def describe_links(self, chat_id):
        """Player sent /me."""
        cards = self.storage.cards_for_chat(chat_id)
        if not cards:
            return "No card is linked to you yet. Send /link and tap your card on the Pi."
        listed = "\n".join(f"• card ending <code>…{c['uid'][-4:]}</code>" for c in cards)
        return f"Linked card(s):\n{listed}\n\nSend /unlink to remove."

    def unlink(self, chat_id):
        """Player sent /unlink."""
        removed = self.storage.unlink_chat(chat_id)
        if self.pending and self.pending["chat_id"] == chat_id:
            self.pending = None
        if not removed:
            return "You didn't have a card linked."
        return f"✅ Unlinked {len(removed)} card(s). You won't receive round results any more."

    def last_round(self, chat_id, first_name):
        """Player sent /last."""
        last = self.storage.last_round_for_chat(chat_id)
        if not last:
            return "No rounds yet. Tap your card on the Pi and start a round!"
        received_at, data = last
        when = time.strftime("%d %b %Y, %H:%M", time.localtime(received_at))
        return format_round(data, first_name) + f"\n\n<i>Played {when}</i>"

    def camera_reply(self, chat_id):
        """Player sent /camera. Returns (reply text, photo bytes or None)."""
        if not self.storage.cards_for_chat(chat_id):
            return ("📷 /camera is for players: link your card first with /link "
                    "(tap it on the reader at the bag)."), None
        report, photo = self._camera_snapshot()
        now = self.wall_clock()
        text = camera_check.full_text(report, now)
        if camera_check.is_fresh(report, now) and report.get("camera"):
            return text, photo
        return text, None            # vision stopped or the camera is gone: never send an old picture

    # ------------------------------------------------------------------------------------
    # Commands for the Pi's owner only: /status and /shutdown (see pi_admin.py)
    # ------------------------------------------------------------------------------------

    def admin_refusal(self, chat_id):
        """None if this chat is the Pi's owner; otherwise the reply that says how to become one."""
        if chat_id in self.admin_chat_ids:
            return None
        return ("🔒 Only the Pi's owner can use this.\n\n"
                f"Your chat id is <code>{chat_id}</code>. To allow this chat, add it to "
                "<code>ADMIN_CHAT_IDS</code> in <code>telegram_bot/.env</code> on the Pi, "
                "then restart the bot (<code>sudo systemctl restart southpaw-bot</code>).")

    def shutdown_reply(self, chat_id, args):
        """/shutdown asks first; /shutdown yes really does it. Returns (reply text, shut down now?)."""
        refusal = self.admin_refusal(chat_id)
        if refusal:
            return refusal, False
        if [a.lower() for a in args or []] != ["yes"]:
            return ("🔌 Switch the Pi off? A game in progress stops, and nothing works again until the "
                    "power is unplugged and plugged back in.\n\nSend <b>/shutdown yes</b> to confirm."), False
        log.warning("Shutdown requested by chat %s", chat_id)
        return ("🔌 <b>Shutting down…</b>\n\n"
                "Unplug the Pi when its <b>green light stops flickering</b> (about 20 s; only the red light "
                "stays on).\n\nTo play again: plug the power back in and wait about a minute. "
                "Everything starts by itself."), True

    def _camera_snapshot(self):
        """Vision's latest (report, photo). Never crashes the bot if something is odd."""
        try:
            return self.read_camera()
        except Exception as error:
            log.warning("Could not read the camera picture: %s", error)
            return None, None

    # ------------------------------------------------------------------------------------
    # Messages from the hub (called by bot.py's UDP listener)
    # ------------------------------------------------------------------------------------

    async def handle_hub_message(self, message):
        """Dispatch one message from the hub or NFC reader. Bad messages are logged and ignored."""
        if not isinstance(message, dict):
            log.warning("Ignoring hub message that isn't a JSON object: %r", message)
            return
        # The NFC reader sends the hub's protocol format, {"uid": ...} with no "kind":
        # the exact same message goes to the hub (port 5006) and to this bot (port 5007)
        kind = message.get("kind", "card" if "uid" in message else None)
        try:
            if kind == "card":
                await self.card_tapped(message["uid"])
            elif kind == "round":
                await self.round_finished(message["uid"], message.get("round") or {})
            else:
                log.info("Ignoring hub message of kind %r", kind)
        except (KeyError, ValueError) as error:
            log.warning("Bad hub message %r: %s", message, error)

    async def card_tapped(self, uid):
        """A card was tapped on the reader."""
        uid = normalise_uid(uid)
        now = self.clock()

        # 1. Someone is waiting to link: this tap is theirs
        if self.pending and self.pending["expires"] > now:
            p, self.pending = self.pending, None
            previous = self.storage.link_card(uid, p["chat_id"], p["username"], p["first_name"])
            log.info("Linked card %s to chat %s", uid, p["chat_id"])
            await self._safe_send(p["chat_id"], f"✅ <b>Card linked!</b> (card ending <code>…{uid[-4:]}</code>)\n\n"
                                                "Your round results will be sent here.")
            if previous:   # the card used to belong to someone else: tell them
                await self._safe_send(previous, f"ℹ️ The card ending <code>…{uid[-4:]}</code> was linked to "
                                                "another Telegram account, so you won't receive its results any more.")
            return
        self.pending = None   # an expired link request is dropped

        # 2. A known player: say hello so they know the Pi recognised them
        player = self.storage.player_for_card(uid)
        if player:
            name = html.escape(player["first_name"] or "there")
            text = f"👋 Hi {name}, card scanned! Punch the bag <b>5 times</b> to start your round."
            line = camera_check.short_line(self._camera_snapshot()[0], self.wall_clock())
            if line:   # vision is running: say whether it can see the player
                text += "\n\n" + line
            await self._safe_send(player["chat_id"], text)
        else:
            log.info("Unknown card %s tapped (not linked to anyone)", uid)

    async def round_finished(self, uid, data):
        """A round finished: store it, and send the summary to the card's owner."""
        uid = normalise_uid(uid)
        player = self.storage.player_for_card(uid)
        chat_id = player["chat_id"] if player else None
        self.storage.save_round(uid, chat_id, data)
        if not player:
            log.info("Round for unlinked card %s stored but not sent", uid)
            return False
        sent = await self._safe_send(chat_id, format_round(data, player["first_name"]))
        log.info("Round summary for card %s %s chat %s", uid, "sent to" if sent else "FAILED for", chat_id)
        return sent

    async def _safe_send(self, chat_id, text):
        """Send, but never crash the bot if Telegram refuses (e.g. the player blocked the bot)."""
        try:
            await self.send(chat_id, text)
            return True
        except Exception as error:      # network down, bot blocked, ... - log and carry on
            log.warning("Could not send to chat %s: %s", chat_id, error)
            return False
