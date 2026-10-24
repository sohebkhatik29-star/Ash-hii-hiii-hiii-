# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: bot.py
# Purpose: Clean Master Entry Point. Registers all modular handlers.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from configs import Config
from handlers.database import db
from handlers.check_user_status import handle_user_status
from handlers.session_manager import session_mgr
from handlers.save_media import handle_incoming_movie_file, handle_generate_links_action
from handlers.start_handler import register_start_handlers
from handlers.admin_commands import register_admin_handlers
from handlers.session_callbacks import register_callback_handlers
from handlers.group_filter import register_group_filter_handlers
from handlers.moderation import register_moderation_handlers

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("AshMovieBot")

Bot = Client(
    name=Config.BOT_USERNAME or "AshMovieBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=100,
    plugins=dict(root="handlers") if False else None,
)

# 1. Global private chat status / ban check
@Bot.on_message(filters.private, group=-1)
async def private_status_guard(bot: Client, cmd: Message):
    """Enforces banned user checks and logs new users."""
    try:
        if cmd.from_user and await handle_user_status(bot, cmd):
            await cmd.reply_text("🚫 **Aapko is bot se ban kiya gaya hai.**", quote=True)
            return
    except Exception as e:
        logger.debug(f"status guard: {e}")

# 2. Movie Upload Session Commands: /addmovie, /finish, /cancel
@Bot.on_message(filters.command(["addmovie", "newmovie"]) & filters.private)
async def addmovie_command(bot: Client, cmd: Message):
    try:
        admin_id = cmd.from_user.id if cmd.from_user else 0
        if admin_id not in Config.ADMINS:
            await cmd.reply_text("⚠️ Sirf admin /addmovie use kar sakte hain.", quote=True)
            return
        status_msg = await cmd.reply_text(
            "🎬 <b>Movie Upload Session Started</b>\n\n"
            "Pehli video file abhi bhejo.\n"
            "Uske baad same movie ki aur qualities add kar sakte ho, ya Generate Links dabao.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Session", callback_data="session_cancel")]
            ]),
            quote=True,
        )
        await session_mgr.start_addmovie_session(admin_id, status_msg.id, cmd.chat.id)
    except Exception as e:
        logger.error(f"addmovie_command error: {e}", exc_info=True)
        try:
            await cmd.reply_text(f"❌ Error: <code>{e}</code>", quote=True)
        except Exception:
            pass

@Bot.on_message(filters.command("cancel") & filters.private)
async def cancel_session_command(bot: Client, cmd: Message):
    """Admin command to cancel active movie session."""
    admin_id = cmd.from_user.id if cmd.from_user else 0
    if admin_id not in Config.ADMINS:
        return
    await session_mgr.cancel_session(admin_id)
    await cmd.reply_text("❌ <b>Movie upload session cancelled.</b> (No new files will be added)", quote=True)

@Bot.on_message(filters.command(["finish", "generatelink"]) & filters.private)
async def finish_session_command(bot: Client, cmd: Message):
    """Admin command to finish session and generate links."""
    admin_id = cmd.from_user.id if cmd.from_user else 0
    if admin_id not in Config.ADMINS:
        return
    await handle_generate_links_action(bot, cmd.chat.id, admin_id)

# 3. Media Upload Listener (video, document, or any media in private)
@Bot.on_message(
    (filters.video | filters.document | filters.animation | filters.audio | filters.media)
    & filters.private
    & ~filters.command(["start", "help", "addmovie", "newmovie", "cancel", "finish", "generatelink"])
)
async def auto_movie_upload_listener(bot: Client, message: Message):
    """Handles incoming movie video/document file uploads within active session."""
    try:
        logger.info(
            f"[MEDIA] private media from {message.from_user.id if message.from_user else None} "
            f"video={bool(message.video)} doc={bool(message.document)} "
            f"media_group={message.media_group_id}"
        )
        if not (message.video or message.document):
            return
        await handle_incoming_movie_file(bot, message)
    except Exception as e:
        logger.error(f"auto_movie_upload_listener error: {e}", exc_info=True)
        try:
            await message.reply_text(f"❌ Upload error: <code>{e}</code>", quote=True)
        except Exception:
            pass

# 4. Register Modular Handlers
register_start_handlers(Bot)
register_admin_handlers(Bot)
register_callback_handlers(Bot)
register_group_filter_handlers(Bot)
register_moderation_handlers(Bot)

# 5. Application Lifecycle & Startup Notification
async def main():
    logger.info("Initializing Ash Movie Store Bot...")
    await Bot.start()
    try:
        await db.init_indexes()
        await session_mgr.restore_from_db()
        await db.sync_from_group_and_channel(Bot)
        logger.info("MongoDB indexes, active sessions, and group movie catalog synced successfully!")
    except Exception as e:
        logger.error(f"Startup warning: {e}")

    if Config.LOG_CHANNEL:
        try:
            bot_info = await Bot.get_me()
            await Bot.send_message(
                chat_id=Config.LOG_CHANNEL,
                text=f"🎬 <b>@{bot_info.username or Config.BOT_USERNAME} is RESTARTED ✅</b>\n\n"
                     f"⚡ <b>Status:</b> Live & Operational\n"
                     f"🍿 <b>Branding:</b> {Config.BRAND_NAME}\n"
                     f"👑 <b>Owner:</b> @{Config.OWNER_USERNAME}"
            )
            logger.info("Restart notification sent to LOG_CHANNEL")
        except Exception as err:
            logger.warning(f"Could not send restart log to LOG_CHANNEL: {err}")

    logger.info("Bot is fully online. Waiting for updates...")
    await idle()
    await Bot.stop()

if __name__ == "__main__":
    Bot.run(main())
