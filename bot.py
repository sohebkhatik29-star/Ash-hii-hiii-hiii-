# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: bot.py
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import Message, BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeAllChatAdministrators, BotCommandScopeChat, BotCommandScopeChatAdministrators
from pyrogram.enums import ParseMode

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
from handlers.force_sub_handler import register_force_sub_handlers
from handlers.admin_panel import register_admin_panel_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

Bot = Client(
    name="AshMovieStoreBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=Config.BOT_WORKERS,
    plugins=None,
    parse_mode=ParseMode.HTML,
)

@Bot.on_message(filters.private & filters.incoming, group=-1)
async def _user_status(client: Client, message: Message):
    await handle_user_status(client, message)

@Bot.on_message(
    filters.private
    & filters.user(Config.ADMINS)
    & (filters.video | filters.document | filters.media)
    & ~filters.command(["start", "help", "addmovie", "cancel", "finish"])
)
async def _incoming_movie(client: Client, message: Message):
    await handle_incoming_movie_file(client, message)

register_start_handlers(Bot)
register_admin_handlers(Bot)
register_callback_handlers(Bot)
register_group_filter_handlers(Bot)
register_moderation_handlers(Bot)
register_force_sub_handlers(Bot)
register_admin_panel_handlers(Bot)

async def setup_bot_commands(bot: Client):
    private_cmds = [
        BotCommand("start", "Start the bot / Home"),
        BotCommand("addmovie", "Start movie upload session"),
        BotCommand("cancel", "Cancel active session"),
        BotCommand("finish", "Generate links / finish session"),
        BotCommand("help", "Help & commands"),
    ]
    group_cmds = [
        BotCommand("addmovie", "Start movie upload session"),
        BotCommand("cancel", "Cancel active session"),
        BotCommand("finish", "Generate links / finish session"),
    ]
    try:
        await bot.delete_bot_commands(scope=BotCommandScopeDefault())
    except Exception:
        try:
            await bot.set_bot_commands([], scope=BotCommandScopeDefault())
        except Exception as e:
            logger.warning(f"clear default: {e}")
    try:
        await bot.set_bot_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
    except Exception as e:
        logger.warning(f"private cmds: {e}")
    try:
        await bot.set_bot_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
        logger.info("✅ Group commands set")
    except Exception as e:
        logger.warning(f"AllGroupChats cmds failed: {e}")
    try:
        await bot.set_bot_commands(group_cmds, scope=BotCommandScopeAllChatAdministrators())
    except Exception as e:
        logger.warning(f"AllChatAdministrators: {e}")
    try:
        gid = int(getattr(Config, "MOVIE_GROUP_ID", 0) or 0)
        if gid:
            try:
                await bot.set_bot_commands(group_cmds, scope=BotCommandScopeChat(chat_id=gid))
            except Exception as e1:
                logger.warning(f"ScopeChat: {e1}")
            try:
                await bot.set_bot_commands(group_cmds, scope=BotCommandScopeChatAdministrators(chat_id=gid))
            except Exception as e2:
                logger.warning(f"ChatAdministrators: {e2}")
    except Exception as e:
        logger.warning(f"group-specific cmds: {e}")

async def main():
    logger.info("Initializing Ash Movie Store Bot...")
    await Bot.start()
    try:
        await db.init_indexes()
        await session_mgr.restore_from_db()
        await db.sync_from_group_and_channel(Bot)
    except Exception as e:
        logger.error(f"Startup warning: {e}")
    await setup_bot_commands(Bot)
    if Config.LOG_CHANNEL:
        try:
            bot_info = await Bot.get_me()
            await Bot.send_message(
                chat_id=Config.LOG_CHANNEL,
                text=f"🎬 <b>@{bot_info.username or Config.BOT_USERNAME} is RESTARTED ✅</b>\n\n"
                     f"⚡ <b>Status:</b> Live (v3)\n"
                     f"🍿 <b>{Config.BRAND_NAME}</b>\n"
                     f"👑 @{Config.OWNER_USERNAME}"
            )
        except Exception as err:
            logger.warning(f"LOG_CHANNEL: {err}")
    logger.info("Bot is fully online (v3).")
    await idle()
    await Bot.stop()

if __name__ == "__main__":
    Bot.run(main())
