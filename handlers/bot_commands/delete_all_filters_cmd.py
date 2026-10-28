# ==============================================================================
# handlers/bot_commands/delete_all_filters_cmd.py
# Command: /delete_all_filters
# Group me command → admin command delete → Yes/No confirm
#   Yes → saare filters delete
#   No  → cancel
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import QueryIdInvalid

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
    return await _is_authorized_uid(client, message.chat.id, message.from_user.id)


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


async def _safe_answer(query: CallbackQuery, text: str = "", show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except QueryIdInvalid:
        pass
    except Exception:
        pass


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

            admin_id = int(message.from_user.id)

            try:
                await message.delete()
            except Exception as e:
                logger.warning("delete_all_filters: delete cmd failed: %s", e)

            # Underscore format — no colon (avoids parse issues)
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Yes", callback_data=f"dafyes_{admin_id}"),
                    InlineKeyboardButton("No", callback_data=f"dafno_{admin_id}"),
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

    # group=-5 → runs BEFORE session_callbacks catch-all (group=0)
    @bot.on_callback_query(group=-5)
    async def delete_all_filters_callback(client: Client, query: CallbackQuery):
        data = (query.data or "").strip()
        if not (data.startswith("dafyes_") or data.startswith("dafno_") or data.startswith("daf_")):
            return  # not our button — let other handlers run

        try:
            logger.info("delete_all_filters callback data=%s user=%s", data, getattr(query.from_user, "id", None))

            action = None
            owner_id = None

            if data.startswith("dafyes_"):
                action = "yes"
                try:
                    owner_id = int(data.split("_", 1)[1])
                except Exception:
                    owner_id = None
            elif data.startswith("dafno_"):
                action = "no"
                try:
                    owner_id = int(data.split("_", 1)[1])
                except Exception:
                    owner_id = None
            elif data.startswith("daf_yes:") or data.startswith("daf_yes_"):
                action = "yes"
                try:
                    owner_id = int(data.replace("daf_yes:", "").replace("daf_yes_", ""))
                except Exception:
                    owner_id = None
            elif data.startswith("daf_no:") or data.startswith("daf_no_"):
                action = "no"
                try:
                    owner_id = int(data.replace("daf_no:", "").replace("daf_no_", ""))
                except Exception:
                    owner_id = None

            if action is None:
                return

            clicker = query.from_user.id if query.from_user else 0
            chat_id = query.message.chat.id if query.message else 0

            # Owner of the prompt OR any authorized admin can click
            allowed = False
            if owner_id and clicker == owner_id:
                allowed = True
            if not allowed:
                allowed = await _is_authorized_uid(client, chat_id, clicker)

            if not allowed:
                await _safe_answer(query, "Only admin can confirm.", show_alert=True)
                return

            if action == "no":
                await _safe_answer(query, "Cancelled")
                try:
                    await query.message.edit_text("Cancelled. No filters were deleted.")
                except Exception:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                return

            # action == yes
            await _safe_answer(query, "Deleting all filters...")
            count = await _wipe_all_filters()
            text = f"All filters deleted.\n\nTotal removed: {count}"
            try:
                await query.message.edit_text(text)
            except Exception:
                try:
                    await client.send_message(chat_id, text)
                except Exception as e:
                    logger.error("delete_all_filters: result send failed: %s", e)

        except Exception as e:
            logger.exception("delete_all_filters_callback: %s", e)
            await _safe_answer(query, "Error occurred", show_alert=True)

    logger.info("/delete_all_filters registered (Yes/No confirm, callback group=-5)")
