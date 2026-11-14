"""
core.py - Southpaw OS Telegram bot

The bot's logic, kept separate from the Telegram plumbing (bot.py) so it can be
tested without a real Telegram bot. It handles:

  * LINKING a player's NFC card to their Telegram chat:
      player sends /link  ->  "tap your card within 60 s"  ->  card tapped on the Pi
      ->  card UID saved against their chat  ->  "Card linked!"
    Only one link can be waiting at a time (there is one card reader). If a second
    player sends /link, they take over and the first is told to try again.

  * Messages from the HUB (Member C) on the Pi:
      {"kind": "card",  "uid": "04A1B2C3"}                     a card was tapped
      {"kind": "round", "uid": "04A1B2C3", "round": {...}}     a round finished: send the summary

Why a Telegram bot can't just use @usernames: bots may only message people who have
opened the bot and pressed Start, and they do it by numeric chat id. That's why each
player links their card once with /link.
"""

import html
import logging
import time

from formatting import format_round
from storage import normalise_uid

log = logging.getLogger("southpaw.core")


class SouthpawBot:
    def __init__(self, storage, send, link_timeout_s=60, clock=time.monotonic):
        """
        storage: a Storage (storage.py)
        send:    async function send(chat_id, html_text) that delivers a Telegram message
        """
        self.storage = storage
        self.send = send
        self.link_timeout_s = link_timeout_s
        self.clock = clock
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

    # ------------------------------------------------------------------------------------
    # Messages from the hub (called by bot.py's UDP listener)
    # ------------------------------------------------------------------------------------

    async def handle_hub_message(self, message):
        """Dispatch one message from the hub. Bad messages are logged and ignored."""
        if not isinstance(message, dict):
            log.warning("Ignoring hub message that isn't a JSON object: %r", message)
            return
        kind = message.get("kind")
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
            await self._safe_send(player["chat_id"],
                                  f"👋 Hi {name}, card scanned! Punch the bag <b>5 times</b> to start your round.")
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
