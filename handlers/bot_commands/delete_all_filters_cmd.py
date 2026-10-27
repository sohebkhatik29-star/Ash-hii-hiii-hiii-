# ==============================================================================
# handlers/bot_commands/delete_all_filters_cmd.py
# Command: /delete_all_filters
# Group me command → admin command delete → Yes/No confirm
#   Yes → saare filters delete
#   No  → cancel
# Unique name so purane moderation handler se clash na ho.
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from configs import Config
from handlers.database import db

logger = logging.getLogger("DeleteAllFiltersCmd")

CONFIRM_TEXT = (
    "Are you 100% sure you want to delete ALL filters?\n\n"
    "This will permanently remove every movie from the filter list. "
    "This action cannot be undone."
)


async def _is_authorized(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    user_id = message.from_user.id

    try:
        from handlers.admins_db import sync_admins_to_config
        await sync_admins_to_config()
    except Exception:
        pass

    try:
        if user_id in (Config.ADMINS or set()):
            return True
    except Exception:
        pass

    try:
        if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            member = await client.get_chat_member(message.chat.id, user_id)
            st = member.status
            if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
            st_s = str(getattr(st, "value", st) or st).lower()
            if any(x in st_s for x in ("administrator", "owner", "creator")):
                return True
    except Exception as e:
        logger.debug("delete_all_filters admin check: %s", e)

    return False


async def _is_authorized_uid(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        from handlers.admins_db import sync_admins_to_config
        await sync_admins_to_config()
    except Exception:
        pass

    try:
        if user_id in (Config.ADMINS or set()):
            return True
    except Exception:
        pass

    try:
        member = await client.get_chat_member(chat_id, user_id)
        st = member.status
        if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
        st_s = str(getattr(st, "value", st) or st).lower()
        if any(x in st_s for x in ("administrator", "owner", "creator")):
            return True
    except Exception as e:
        logger.debug("delete_all_filters uid admin check: %s", e)

    return False


async def _wipe_all_filters() -> int:
    """Delete every movie/filter. Returns count removed."""
    count = 0
    try:
        if getattr(db, "has_mongo", False) and getattr(db, "movies_col", None) is not None:
            try:
                count = await db.movies_col.count_documents({})
            except Exception:
                count = 0
            try:
                await db.movies_col.delete_many({})
            except Exception as e:
                logger.error("delete_all_filters delete_many: %s", e)
        mem = getattr(db, "_mem_movies", None)
        if isinstance(mem, dict):
            if not count:
                count = len(mem)
            mem.clear()
    except Exception as e:
        logger.exception("delete_all_filters wipe: %s", e)
    return int(count or 0)


def register_delete_all_filters_command(bot: Client):
    """Register /delete_all_filters with Yes/No confirmation."""

    @bot.on_message(
        filters.command(["delete_all_filters"])
        & (filters.group | filters.private),
        group=-3,
    )
    async def delete_all_filters_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return

            if not await _is_authorized(client, message):
                return

            admin_id = message.from_user.id

            try:
                await message.delete()
            except Exception as e:
                logger.warning("delete_all_filters: delete cmd failed: %s", e)

            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes", callback_data=f"daf_yes:{admin_id}"),
                    InlineKeyboardButton("No", callback_data=f"daf_no:{admin_id}"),
                ]
            ])
            try:
                await client.send_message(
                    chat_id=message.chat.id,
                    text=CONFIRM_TEXT,
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error("delete_all_filters: confirm send failed: %s", e)

        except Exception as e:
            logger.exception("delete_all_filters_handler: %s", e)

    @bot.on_callback_query(filters.regex(r"^daf_(yes|no):(\d+)$"))
    async def delete_all_filters_callback(client: Client, query: CallbackQuery):
        try:
            data = query.data or ""
            parts = data.split(":", 1)
            if len(parts) != 2:
                await query.answer()
                return
            action, owner_s = parts[0], parts[1]
            try:
                owner_id = int(owner_s)
            except ValueError:
                await query.answer()
                return

            clicker = query.from_user.id if query.from_user else 0

            if clicker != owner_id:
                if not await _is_authorized_uid(client, query.message.chat.id, clicker):
                    await query.answer("Only admin can confirm.", show_alert=True)
                    return

            if not await _is_authorized_uid(client, query.message.chat.id, clicker):
                await query.answer("Not authorized.", show_alert=True)
                return

            if action == "daf_no":
                try:
                    await query.message.edit_text("Cancelled. No filters were deleted.")
                except Exception:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                await query.answer("Cancelled")
                return

            await query.answer("Deleting all filters...")
            count = await _wipe_all_filters()
            try:
                await query.message.edit_text(
                    f"All filters deleted.\n\nTotal removed: {count}"
                )
            except Exception:
                try:
                    await client.send_message(
                        query.message.chat.id,
                        f"All filters deleted.\n\nTotal removed: {count}",
                    )
                except Exception as e:
                    logger.error("delete_all_filters: result send failed: %s", e)

        except Exception as e:
            logger.exception("delete_all_filters_callback: %s", e)
            try:
                await query.answer("Error", show_alert=True)
            except Exception:
                pass

    logger.info("/delete_all_filters registered (Yes/No confirm)")
