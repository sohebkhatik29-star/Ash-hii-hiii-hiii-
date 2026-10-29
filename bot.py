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
    name=Config.BOT_USERNAME or "AshMovieBot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    workers=100,
    plugins=dict(root="handlers") if False else None,
)

@Bot.on_message(filters.private, group=-1)
async def private_status_guard(bot: Client, cmd: Message):
    try:
        if cmd.from_user and await handle_user_status(bot, cmd):
            await cmd.reply_text("\U0001f6ab **Aapko is bot se ban kiya gaya hai.**", quote=True)
            return
    except Exception as e:
        logger.debug(f"status guard: {e}")

@Bot.on_message(filters.command(["addmovie", "newmovie"]) & filters.private)
async def addmovie_command(bot: Client, cmd: Message):
    try:
        admin_id = cmd.from_user.id if cmd.from_user else 0
        if admin_id not in Config.ADMINS:
            await cmd.reply_text("\u26a0\ufe0f Sirf admin /addmovie use kar sakte hain.", quote=True)
            return
        status_msg = await cmd.reply_text(
            "\U0001f3ac <b>Movie Upload Session Started</b> (v3)\n\n"
            "Pehli video file bhejo. Caption optional hai.\n"
            "Jab saari files aa jayein to <b>/finish</b> ya Generate Links dabao.",
            quote=True,
        )
        await session_mgr.start_session(admin_id, status_msg.id if status_msg else None)
    except Exception as e:
        logger.exception(f"addmovie: {e}")

@Bot.on_message(filters.command(["finish", "done_upload"]) & filters.private)
async def finish_command(bot: Client, cmd: Message):
    try:
        await handle_generate_links_action(bot, cmd)
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
    logger.info("/premium command registered")
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
    logger.info("/cleangroup command registered")
except Exception as e:
    logger.error(f"cleangroup_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.antilink_cmd import register_antilink_command
    register_antilink_command(Bot)
    logger.info("antilink + /unbanlink registered")
except Exception as e:
    logger.error(f"antilink_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.bot_commands.warn_cmd import register_warn_command
    register_warn_command(Bot)
    logger.info("/warn command registered")
except Exception as e:
    logger.error(f"warn_cmd register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.indexing import register_indexing_handlers
    register_indexing_handlers(Bot)
except Exception as e:
    logger.error(f"indexing register failed (bot still runs): {type(e).__name__}: {e}")

try:
    from handlers.admin_panel import register_admin_panel_handlers
    register_admin_panel_handlers(Bot)
except Exception as e:
    logger.error(f"admin_panel register failed (bot still runs): {type(e).__name__}: {e}")

async def setup_bot_commands(bot: Client):
    group_cmds = [
        BotCommand("start", "Bot start / welcome"),
        BotCommand("ban", "User ban (reply / user_id)"),
        BotCommand("unban", "User unban (reply / user_id)"),
        BotCommand("done", "Not-found session close"),
        BotCommand("chakspelling", "Check spelling (reply to user)"),
        BotCommand("deletefilter", "Reply to poster remove filter"),
        BotCommand("not_available", "Not available on Telegram (reply)"),
        BotCommand("premium", "Premium only contact @movies_1780"),
        BotCommand("deleteallfilters", "Delete ALL filters (confirm)"),
        BotCommand("cleangroup", "Clean group messages (admin)"),
        BotCommand("unbanlink", "Unban after link suspend/ban"),
        BotCommand("warn", "Warn user (reply + reason)"),
    ]
    private_cmds = [
        BotCommand("start", "Bot start / welcome"),
        BotCommand("help", "Help & how to use"),
        BotCommand("addmovie", "Start movie upload session"),
        BotCommand("cancel", "Cancel upload session"),
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
        logger.info("Group commands set")
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
                text=f"\U0001f3ac <b>@{bot_info.username or Config.BOT_USERNAME} is RESTARTED \u2705</b>\n\n"
                     f"\u26a1 <b>Status:</b> Live (v3)\n"
                     f"\U0001f37f <b>{Config.BRAND_NAME}</b>\n"
                     f"\U0001f451 @{Config.OWNER_USERNAME}"
            )
        except Exception as err:
            logger.warning(f"LOG_CHANNEL: {err}")
    logger.info("Bot is fully online (v3).")
    await idle()
    await Bot.stop()

if __name__ == "__main__":
    Bot.run(main())
