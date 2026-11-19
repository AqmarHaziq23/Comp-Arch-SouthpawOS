"""
bot.py - Southpaw OS Telegram bot (run this)

Sends each player their boxing round results on Telegram.

Runs as its own program on the Pi (like vision): if it stops, the game still works.
It does two things at once:
  1. Talks to Telegram ("long polling": it keeps asking Telegram for new messages, so it
     works on a phone hotspot - no port forwarding or public address needed).
  (/camera also reads vision's latest picture from a folder on the Pi: see camera_check.py.)
  2. Listens on UDP 127.0.0.1:5007 (only programs on the same Pi can send to it) for:
        {"uid": "04A1B2C3"}                                      NFC reader: a card was tapped
        {"kind": "round", "uid": "04A1B2C3", "round": {...}}     hub: a round finished
     (Port 5006 is the hub's own NFC port; the NFC reader sends each tap to both.)

To test without the NFC reader, use simulate.py; for rounds you can also run the hub's
simulator: python -m southpaw play --sim --card 04A1B2C3 (in hub/).

Setup: copy .env.example to .env and put your bot token from @BotFather in it.
Run:   python bot.py
"""

import asyncio
import html
import json
import logging
import os

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import pi_admin
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
    port = int(os.environ.get("HUB_LISTEN_PORT", "5007"))
    storage = Storage(os.environ.get("DB_PATH", "players.db"))

    app = Application.builder().token(token).build()

    async def send(chat_id, text):
        """How the core sends a message: Telegram HTML formatting."""
        await app.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    core = SouthpawBot(storage, send, link_timeout_s=int(os.environ.get("LINK_TIMEOUT_S", "60")),
                       admin_chat_ids=pi_admin.admin_chat_ids(os.environ.get("ADMIN_CHAT_IDS", "")))

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

    async def cmd_camera(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # A photo from vision with the check as its caption; only the text if there's no fresh picture
        text, photo = core.camera_reply(update.effective_chat.id)
        if photo:
            await update.message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML)
        else:
            await reply(update, text)

    # --- Owner-only commands (ADMIN_CHAT_IDS in .env; not in the players' command menu) ----
    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        refusal = core.admin_refusal(update.effective_chat.id)
        if refusal:
            await reply(update, refusal)
            return
        # Asking the Pi takes a moment: do it outside the bot's main loop so other messages aren't held up
        ips = await asyncio.to_thread(pi_admin.ip_addresses)
        states = await asyncio.to_thread(pi_admin.service_states)
        await reply(update, pi_admin.status_text(ips, states, pi_admin.uptime_s()))

    async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text, go = core.shutdown_reply(update.effective_chat.id, context.args)
        await reply(update, text)
        if not go:
            return
        # Wait a few seconds first, so Telegram marks this message as handled. (After the next boot,
        # drop_pending_updates below also skips anything left over, so it can't shut down again.)
        await asyncio.sleep(3)
        ok, error = await asyncio.to_thread(pi_admin.power_off)
        if not ok:
            log.warning("Shutdown failed: %s", error)
            await reply(update, "⚠️ Couldn't switch the Pi off: "
                                f"<code>{html.escape(error[:200])}</code>\n\n"
                                "Has <code>bash telegram_bot/allow_shutdown.sh</code> been run once on the Pi? "
                                "Otherwise use SSH: <code>sudo shutdown now</code>.")

    for name, handler in [("start", cmd_start), ("help", cmd_start), ("link", cmd_link),
                          ("me", cmd_me), ("unlink", cmd_unlink), ("last", cmd_last),
                          ("camera", cmd_camera), ("status", cmd_status), ("shutdown", cmd_shutdown)]:
        app.add_handler(CommandHandler(name, handler))

    # --- Things to do once the bot is connected -------------------------------------------
    async def post_init(application):
        # The command menu players see when they type "/"
        await application.bot.set_my_commands([
            BotCommand("link", "Link your NFC player card"),
            BotCommand("last", "Show your most recent round"),
            BotCommand("camera", "Check the camera can see you (photo)"),
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
        # drop_pending_updates: messages sent while the bot was off are skipped when it starts, so an
        # old /link (or a /shutdown that switched the Pi off) isn't acted on minutes or hours later
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)   # runs until Ctrl+C
    finally:
        storage.close()


if __name__ == "__main__":
    main()
