# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: bot.py
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters, idle
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeChatAdministrators,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)

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

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("AshMovieBot")

Bot = Client(
    "AshMovieBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=100,
    max_concurrent_transmissions=10,
)


@Bot.on_message(filters.private, group=-1)
async def user_status_handler(bot: Client, message: Message):
    try:
        await handle_user_status(bot, message)
    except Exception as e:
        logger.exception(f"user_status: {e}")


@Bot.on_message(filters.command(["addmovie", "newmovie"]) & filters.private)
async def addmovie_command(bot: Client, cmd: Message):
    try:
        admin_id = cmd.from_user.id if cmd.from_user else 0
        if admin_id not in Config.ADMINS:
            await cmd.reply_text("\u26a0\ufe0f Sirf admin /addmovie use kar sakte hain.", quote=True)
            return
        status_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Continue Upload", callback_data="session_continue"),
                InlineKeyboardButton("Generate Links", callback_data="session_generate"),
            ],
            [InlineKeyboardButton("Cancel Session", callback_data="session_cancel")],
        ])
        status_msg = await cmd.reply_text(
            "\U0001f3ac <b>Movie Upload Session Started</b> (v3)\n\n"
            "Pehli video file bhejo. Caption optional hai.\n"
            "Saari files aa jayein to <b>Generate Links</b> button dabao.",
            reply_markup=status_kb,
            quote=True,
        )
        await session_mgr.start_addmovie_session(admin_id, status_msg.id if status_msg else 0, cmd.chat.id)
    except Exception as e:
        logger.exception(f"addmovie: {e}")


@Bot.on_message(filters.command(["finish", "done_upload"]) & filters.private)
async def finish_command(bot: Client, cmd: Message):
    try:
        admin_id = cmd.from_user.id if cmd.from_user else 0
        await handle_generate_links_action(bot, cmd.chat.id, admin_id)
    except Exception as e:
        logger.exception(f"finish: {e}")


@Bot.on_message(filters.command(["cancel"]) & filters.private)
async def cancel_command(bot: Client, cmd: Message):
    try:
        admin_id = cmd.from_user.id if cmd.from_user else 0
        await session_mgr.cancel_session(admin_id)
        await cmd.reply_text("\u274c Session cancel ho gaya.", quote=True)
    except Exception as e:
        logger.exception(f"cancel: {e}")


@Bot.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.animation))
async def private_media_handler(bot: Client, message: Message):
    try:
        await handle_incoming_movie_file(bot, message)
    except Exception as e:
        logger.exception(f"private media: {e}")


register_start_handlers(Bot)
register_admin_handlers(Bot)
register_callback_handlers(Bot)
register_group_filter_handlers(Bot)
register_moderation_handlers(Bot)

register_force_sub_handlers(Bot)

# Channel auto-index (alag file — watch channel pe auto save + post)
try:
    from handlers.channel_auto_index import register_channel_auto_index
    register_channel_auto_index(Bot)
except Exception as _e:
    logging.getLogger("AshMovieBot").warning("channel_auto_index not loaded: %s", _e)

try:
    from handlers.bot_commands.done_cmd import register_done_command
    register_done_command(Bot)
except Exception as e:
    logger.error(f"done_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.chakspelling_cmd import register_chakspelling_command
    register_chakspelling_command(Bot)
except Exception as e:
    logger.error(f"chakspelling_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.delete_filter_cmd import register_delete_filter_command
    register_delete_filter_command(Bot)
except Exception as e:
    logger.error(f"delete_filter_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.notavailable_cmd import register_notavailable_command
    register_notavailable_command(Bot)
except Exception as e:
    logger.error(f"notavailable_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.premium_cmd import register_premium_command
    register_premium_command(Bot)
except Exception as e:
    logger.error(f"premium_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.delete_all_filters_cmd import register_delete_all_filters_command
    register_delete_all_filters_command(Bot)
except Exception as e:
    logger.error(f"delete_all_filters_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.cleangroup_cmd import register_cleangroup_command
    register_cleangroup_command(Bot)
except Exception as e:
    logger.error(f"cleangroup_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.warn_cmd import register_warn_command
    register_warn_command(Bot)
except Exception as e:
    logger.error(f"warn_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.antilink_cmd import register_antilink_handlers
    register_antilink_handlers(Bot)
except Exception as e:
    logger.error(f"antilink_cmd register failed (bot still runs): {type(e).__name__}: {e}")


async def setup_bot_commands():
    try:
        private_cmds = [
            BotCommand("start", "Start the bot"),
            BotCommand("addmovie", "Start movie upload session"),
            BotCommand("finish", "Finish upload and generate links"),
            BotCommand("cancel", "Cancel current session"),
            BotCommand("status", "Show bot status"),
        ]
        await Bot.set_bot_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
        logger.info("Private commands set")
    except Exception as e:
        logger.warning(f"set private commands: {e}")
    try:
        group_cmds = [
            BotCommand("find", "Search movie"),
            BotCommand("cleangroup", "Clean group messages"),
        ]
        await Bot.set_bot_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
        logger.info("Group commands set")
    except Exception as e:
        logger.warning(f"set group commands: {e}")
    try:
        gid = int(getattr(Config, "MOVIE_GROUP_ID", 0) or 0)
        if gid:
            admin_cmds = [
                BotCommand("warn", "Warn a user"),
                BotCommand("cleangroup", "Clean group messages"),
            ]
            await Bot.set_bot_commands(admin_cmds, scope=BotCommandScopeChatAdministrators(chat_id=gid))
    except Exception as e:
        logger.warning(f"set admin commands: {e}")


async def main():
    await db.connect()
    await Bot.start()
    try:
        await setup_bot_commands()
    except Exception as e:
        logger.warning(f"setup commands: {e}")
    me = await Bot.get_me()
    logger.info("Bot is fully online (v3). @%s", me.username)
    await idle()
    await Bot.stop()


if __name__ == "__main__":
    try:
        asyncio.get_event_loop().run_until_complete(main())
    except KeyboardInterrupt:
        pass
