"""
bot.py - Southpaw OS Telegram bot (run this)

Sends each player their boxing round results on Telegram.

Runs as its own program on the Pi (like vision): if it stops, the game still works.
It does two things at once:
  1. Talks to Telegram ("long polling": it keeps asking Telegram for new messages, so it
     works on a phone hotspot - no port forwarding or public address needed).
  2. Listens for the HUB's messages on UDP 127.0.0.1:5006 (only programs on the same Pi
     can send to it):
        {"kind": "card",  "uid": "04A1B2C3"}                     a card was tapped
        {"kind": "round", "uid": "04A1B2C3", "round": {...}}     a round finished

Until the NFC reader and hub are ready, use simulate.py to pretend to be the hub.

Setup: copy .env.example to .env and put your bot token from @BotFather in it.
Run:   python bot.py
"""

import asyncio
import json
import logging
import os

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from core import SouthpawBot
from storage import Storage

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)      # hide the library's "polling..." spam
log = logging.getLogger("southpaw.bot")


def load_env(path=".env"):
    """Read KEY=value lines from .env into os.environ (lines starting with # are comments)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class HubListener(asyncio.DatagramProtocol):
    """Receives the hub's UDP messages (one JSON object per datagram) and hands them to the core."""

    def __init__(self, core):
        self.core = core

    def datagram_received(self, data, addr):
        try:
            message = json.loads(data)
        except ValueError:
            log.warning("Ignoring non-JSON datagram from %s", addr)
            return
        log.info("Hub message: %s", message.get("kind") if isinstance(message, dict) else message)
        # Handle it in the background so the listener is ready for the next message straight away
        asyncio.get_running_loop().create_task(self.core.handle_hub_message(message))


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token or token.startswith("put-"):
        raise SystemExit("No bot token: copy .env.example to .env and paste your token from @BotFather")
    host = os.environ.get("HUB_LISTEN_HOST", "127.0.0.1")
    port = int(os.environ.get("HUB_LISTEN_PORT", "5006"))
    storage = Storage(os.environ.get("DB_PATH", "players.db"))

    app = Application.builder().token(token).build()

    async def send(chat_id, text):
        """How the core sends a message: Telegram HTML formatting."""
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    core = SouthpawBot(storage, send, link_timeout_s=int(os.environ.get("LINK_TIMEOUT_S", "60")))

    # --- Player commands -------------------------------------------------------------------
    async def reply(update, text):
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await reply(update, core.welcome(update.effective_user.first_name))

    async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        await reply(update, await core.start_link(update.effective_chat.id, user.username, user.first_name))

    async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await reply(update, core.describe_links(update.effective_chat.id))

    async def cmd_unlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await reply(update, core.unlink(update.effective_chat.id))

    async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await reply(update, core.last_round(update.effective_chat.id, update.effective_user.first_name))

    for name, handler in [("start", cmd_start), ("help", cmd_start), ("link", cmd_link),
                          ("me", cmd_me), ("unlink", cmd_unlink), ("last", cmd_last)]:
        app.add_handler(CommandHandler(name, handler))

    # --- Things to do once the bot is connected -------------------------------------------
    async def post_init(application):
        # The command menu players see when they type "/"
        await application.bot.set_my_commands([
            BotCommand("link", "Link your NFC player card"),
            BotCommand("last", "Show your most recent round"),
            BotCommand("me", "Which card is linked to you"),
            BotCommand("unlink", "Stop receiving round results"),
            BotCommand("help", "How this bot works"),
        ])
        # Start listening for the hub's messages
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: HubListener(core), local_addr=(host, port))
        me = await application.bot.get_me()
        log.info("Bot @%s is running. Listening for the hub on udp://%s:%d", me.username, host, port)

    app.post_init = post_init
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)   # runs until Ctrl+C
    finally:
        storage.close()


if __name__ == "__main__":
    main()
