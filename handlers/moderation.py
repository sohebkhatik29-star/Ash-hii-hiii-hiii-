# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/moderation.py
# Purpose: Group moderation commands — /warn /sp /rls /n_available /prm
#          /dl_all_filters, auto-ban after 3 warns, 24h auto-delete of replies.
# ==============================================================================

import asyncio
import logging
import urllib.parse
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from configs import Config
from handlers.database import db

logger = logging.getLogger("Moderation")

# Moderation bot replies auto-delete after 24 hours
MOD_DELETE_SECONDS = 24 * 60 * 60
OWNER_MENTION = f"@{Config.OWNER_USERNAME}" if Config.OWNER_USERNAME else "@movies_1780"


async def _safe_delete(client: Client, chat_id: int, message_ids: list):
    try:
        ids = [i for i in message_ids if i]
        if ids:
            await client.delete_messages(chat_id, ids)
    except Exception as e:
        logger.debug(f"mod safe_delete: {e}")


async def _schedule_delete(client: Client, chat_id: int, message_ids: list, delay: int):
    try:
        await asyncio.sleep(delay)
        await _safe_delete(client, chat_id, message_ids)
    except Exception as e:
        logger.debug(f"mod schedule_delete: {e}")


async def _is_admin(client: Client, message: Message) -> bool:
    """True if Config.ADMINS / owner OR Telegram group admin/owner — full command access."""
    if not message.from_user:
        return False
    user_id = message.from_user.id
    if user_id in Config.ADMINS:
        return True
    if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member = await client.get_chat_member(message.chat.id, user_id)
            return member.status in (
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.OWNER,
            )
        except Exception as e:
            logger.debug(f"admin check failed: {e}")
    return False


def _extract_reason(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return ""


async def _delete_cmd_and_reply(
    client: Client,
    message: Message,
    reply_text: str,
    reply_markup=None,
    quote_to: Message = None,
):
    """Delete admin command, send bot reply, then after 24h delete bot reply + user message together."""
    try:
        await message.delete()
    except Exception:
        pass
    target = quote_to or message.reply_to_message or message
    try:
        bot_msg = await target.reply_text(
            reply_text,
            reply_markup=reply_markup,
            quote=True,
            disable_web_page_preview=True,
        )
        # 24h baad: bot ka reply + user ka original message dono ek sath delete
        ids_to_delete = [bot_msg.id]
        if target and getattr(target, "id", None) and target.id != bot_msg.id:
            ids_to_delete.append(target.id)
        asyncio.create_task(
            _schedule_delete(client, message.chat.id, ids_to_delete, MOD_DELETE_SECONDS)
        )
        return bot_msg
    except Exception as e:
        logger.error(f"mod reply failed: {e}")
        return None


def register_moderation_handlers(bot: Client):

    # ------------------------------------------------------------------
    # /dl_all_filters  — wipe every movie filter from DB
    # ------------------------------------------------------------------
    @bot.on_message(filters.command(["dl_all_filters", "deleteallfilters"]) & filters.group)
    async def dl_all_filters_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        try:
            await message.delete()
        except Exception:
            pass
        count = await db.delete_all_filters()
        try:
            bot_msg = await client.send_message(
                message.chat.id,
                f"🗑 <b>All filters deleted.</b>\n\n"
                f"Total removed: <b>{count}</b>\n"
                f"Group search ab empty hai — naye posts /addmovie se add karo.",
            )
            asyncio.create_task(
                _schedule_delete(client, message.chat.id, [bot_msg.id], MOD_DELETE_SECONDS)
            )
        except Exception as e:
            logger.error(f"dl_all_filters: {e}")

    # ------------------------------------------------------------------
    # /warn [reason]  — reply to user. 3 warns → ban
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("warn") & filters.group)
    async def warn_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        replied = message.reply_to_message
        if not replied or not replied.from_user:
            try:
                await message.delete()
            except Exception:
                pass
            tip = await message.reply_text(
                "⚠️ User ke message pe <b>reply</b> karke /warn likho.\n"
                "Example: <code>/warn ek din me sirf ek request</code>",
                quote=True,
            )
            asyncio.create_task(
                _schedule_delete(client, message.chat.id, [tip.id], 60)
            )
            return

        target = replied.from_user
        # Bot owners, bots, aur group admins ko warn/ban nahi
        if target.is_bot or target.id in Config.ADMINS:
            try:
                await message.delete()
            except Exception:
                pass
            return
        try:
            tm = await client.get_chat_member(message.chat.id, target.id)
            if tm.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                try:
                    await message.delete()
                except Exception:
                    pass
                return
        except Exception:
            pass

        reason = _extract_reason(message) or "ek din me sirf ek request"
        result = await db.add_warning(target.id, reason=reason, admin_id=message.from_user.id)
        warn_count = result["warn_count"]
        is_banned = result["is_banned"]

        mention = target.mention if hasattr(target, "mention") else f"<a href='tg://user?id={target.id}'>{target.first_name or 'User'}</a>"

        if is_banned:
            text = (
                f"🚫 <b>USER BANNED</b>\n\n"
                f"User: {mention}\n"
                f"Warnings: <b>{warn_count}/3</b>\n"
                f"Reason: <code>{reason}</code>\n\n"
                f"Aap ne niyam toda hai — <b>ek din me sirf ek request</b>.\n"
                f"Ab aap group me search nahi kar sakte. Message delete ho jayenge.\n\n"
                f"Unban ke liye owner se baat karo: {OWNER_MENTION}"
            )
        else:
            text = (
                f"⚠️ <b>WARNING {warn_count}/3</b>\n\n"
                f"User: {mention}\n"
                f"Reason: <code>{reason}</code>\n\n"
                f"📌 Rule: <b>Ek din me sirf ek request</b>.\n"
                f"3 warnings ke baad permanent ban ho jayega."
            )

        await _delete_cmd_and_reply(client, message, text, quote_to=replied)

    # ------------------------------------------------------------------
    # /sp  — spelling wrong + Google search button (uses replied text)
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("sp") & filters.group)
    async def spelling_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        replied = message.reply_to_message
        if not replied:
            try:
                await message.delete()
            except Exception:
                pass
            return

        query = (replied.text or replied.caption or "").strip()
        if query.startswith("/"):
            parts = query.split(maxsplit=1)
            query = parts[1] if len(parts) > 1 else query
        search_q = urllib.parse.quote_plus(query or "movie")
        google_url = f"https://www.google.com/search?q={search_q}"

        text = (
            "📝 <b>Aapki spelling galat hai.</b>\n\n"
            "Kripya spelling check karke dubara try karo.\n"
            "Neeche Google button se correct name search kar sakte ho."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Google pe search karo", url=google_url)]
        ])
        await _delete_cmd_and_reply(client, message, text, reply_markup=markup, quote_to=replied)

    # ------------------------------------------------------------------
    # /rls  — movie not released yet
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("rls") & filters.group)
    async def not_released_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        replied = message.reply_to_message
        if not replied:
            try:
                await message.delete()
            except Exception:
                pass
            return
        text = (
            "📅 <b>Movie abhi release nahi hui.</b>\n\n"
            "Baka, movie release hone se pehle dekhne ka try mat karo 😅\n"
            "<b>Your movie is not released yet.</b>"
        )
        await _delete_cmd_and_reply(client, message, text, quote_to=replied)

    # ------------------------------------------------------------------
    # /n_available  — not available on Telegram
    # ------------------------------------------------------------------
    @bot.on_message(filters.command(["n_available", "navail", "notavailable"]) & filters.group)
    async def not_available_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        replied = message.reply_to_message
        if not replied:
            try:
                await message.delete()
            except Exception:
                pass
            return
        text = (
            "❌ <b>This movie is not available on Telegram.</b>\n\n"
            "Yeh movie Telegram pe available nahi hai."
        )
        await _delete_cmd_and_reply(client, message, text, quote_to=replied)

    # ------------------------------------------------------------------
    # /prm  — premium only
    # ------------------------------------------------------------------
    @bot.on_message(filters.command(["prm", "premium"]) & filters.group)
    async def premium_only_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        replied = message.reply_to_message
        if not replied:
            try:
                await message.delete()
            except Exception:
                pass
            return
        text = (
            "⭐ <b>This movie is available on Premium only.</b>\n\n"
            "Yeh movie sirf Premium members ke liye available hai."
        )
        await _delete_cmd_and_reply(client, message, text, quote_to=replied)

    # ------------------------------------------------------------------
    # /unban  — owner/admin unban (reply or /unban <user_id>)
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("unban") & filters.group)
    async def unban_cmd(client: Client, message: Message):
        if not message.from_user or not await _is_admin(client, message):
            return
        target_id = None
        replied = message.reply_to_message
        if replied and replied.from_user:
            target_id = replied.from_user.id
        else:
            parts = (message.text or "").split()
            if len(parts) >= 2 and parts[1].isdigit():
                target_id = int(parts[1])
        try:
            await message.delete()
        except Exception:
            pass
        if not target_id:
            tip = await client.send_message(
                message.chat.id,
                "Usage: user pe reply karke /unban  YA  /unban USER_ID",
            )
            asyncio.create_task(_schedule_delete(client, message.chat.id, [tip.id], 60))
            return
        await db.unban_user(target_id)
        bot_msg = await client.send_message(
            message.chat.id,
            f"✅ User <code>{target_id}</code> unban ho gaya. Ab search kar sakta hai.",
        )
        asyncio.create_task(
            _schedule_delete(client, message.chat.id, [bot_msg.id], MOD_DELETE_SECONDS)
        )
