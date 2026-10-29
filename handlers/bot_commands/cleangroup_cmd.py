# ==============================================================================
# handlers/bot_commands/cleangroup_cmd.py
# /cleangroup  |  /purgeall | /cleangrp
# Group clean with mode buttons. Admin only.
# Bots cannot use get_chat_history — use get_messages / direct delete by IDs.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
try:
    from pyrogram.enums import ChatMembersFilter
except Exception:
    ChatMembersFilter = None
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, QueryIdInvalid

from configs import Config

logger = logging.getLogger("CleanGroupCmd")

_CANCEL: dict = {}
_RUNNING: dict = {}

MAX_SCAN = 5000
BATCH_SIZE = 100


async def _safe_answer(query: CallbackQuery, text: str = "", show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except QueryIdInvalid:
        pass
    except Exception:
        pass


async def _is_admin(client: Client, chat_id: int, user_id: int) -> bool:
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
        if int(user_id) == int(getattr(Config, "OWNER_ID", 0) or 0):
            return True
    except Exception:
        pass
    try:
        m = await client.get_chat_member(chat_id, user_id)
        st = m.status
        if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
        s = str(getattr(st, "value", st) or st).lower()
        if any(x in s for x in ("administrator", "owner", "creator")):
            return True
    except Exception as e:
        logger.warning("is_admin: %s", e)
    return False


async def _get_owner_id(client: Client, chat_id: int):
    try:
        kwargs = {}
        if ChatMembersFilter is not None:
            kwargs["filter"] = ChatMembersFilter.ADMINISTRATORS
        else:
            kwargs["filter"] = "administrators"
        async for m in client.get_chat_members(chat_id, **kwargs):
            st = m.status
            s = str(getattr(st, "value", st) or st).lower()
            if "owner" in s or "creator" in s:
                if m.user:
                    return m.user.id
    except Exception as e:
        logger.warning("get_owner: %s", e)
    return None


async def _bot_can_delete(client: Client, chat_id: int) -> bool:
    try:
        me = await client.get_chat_member(chat_id, "me")
        st = me.status
        s = str(getattr(st, "value", st) or st).lower()
        if "owner" in s or "creator" in s:
            return True
        if "administrator" in s:
            priv = getattr(me, "privileges", None)
            if priv is None:
                return True
            return bool(getattr(priv, "can_delete_messages", False))
    except Exception as e:
        logger.warning("bot_can_delete: %s", e)
    return False


def _label(mode: str) -> str:
    return {
        "users": "Only Users",
        "users_owner": "Users + Owner",
        "all": "Delete ALL",
    }.get(mode, mode)


async def _delete_ids(client: Client, chat_id: int, ids: list) -> tuple:
    """Try batch delete; on failure try one-by-one. Returns (deleted, failed)."""
    if not ids:
        return 0, 0
    try:
        await client.delete_messages(chat_id, ids)
        return len(ids), 0
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            await client.delete_messages(chat_id, ids)
            return len(ids), 0
        except Exception:
            pass
    except Exception as e:
        logger.warning("batch delete fail (%s): %s", len(ids), e)

    deleted = failed = 0
    for mid in ids:
        try:
            await client.delete_messages(chat_id, mid)
            deleted += 1
        except FloodWait as e:
            await asyncio.sleep(int(getattr(e, "value", 3)) + 1)
            try:
                await client.delete_messages(chat_id, mid)
                deleted += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    return deleted, failed


async def _run_clean_all(client: Client, chat_id: int, status_msg: Message):
    """Delete ALL — no get_messages needed, just wipe ID ranges."""
    _RUNNING[chat_id] = True
    _CANCEL[chat_id] = False
    deleted = failed = 0
    top_id = int(getattr(status_msg, "id", 0) or 0)
    if top_id <= 1:
        try:
            await status_msg.edit_text("❌ Status id missing. Dubara /cleangroup.")
        except Exception:
            pass
        _RUNNING[chat_id] = False
        return

    lowest = max(1, top_id - MAX_SCAN)
    current = top_id - 1
    tried = 0

    try:
        while current >= lowest:
            if _CANCEL.get(chat_id):
                break
            ids = []
            for _ in range(BATCH_SIZE):
                if current < lowest:
                    break
                ids.append(current)
                current -= 1
            if not ids:
                break

            tried += len(ids)
            d, f = await _delete_ids(client, chat_id, ids)
            deleted += d
            failed += f

            try:
                await status_msg.edit_text(
                    f"🧹 Cleaning...\nMode: Delete ALL\n"
                    f"Tried IDs: {tried}\nDeleted: {deleted}\nFailed: {failed}"
                )
            except Exception:
                pass
            await asyncio.sleep(0.2)

        final = (
            f"⏹ Cancelled\nTried: {tried}\nDeleted: {deleted}\nFailed: {failed}"
            if _CANCEL.get(chat_id)
            else (
                f"✅ Done\nMode: Delete ALL\n"
                f"Tried IDs: {tried}\nDeleted: {deleted}\nFailed: {failed}\n"
                f"(last ~{MAX_SCAN} ids)"
            )
        )
        try:
            await status_msg.edit_text(final)
        except Exception:
            try:
                await client.send_message(chat_id, final)
            except Exception:
                pass
    except Exception as e:
        logger.exception("run_clean_all: %s", e)
        try:
            await status_msg.edit_text(f"❌ Error: {type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        _RUNNING[chat_id] = False
        _CANCEL[chat_id] = False


async def _run_clean_filtered(client: Client, chat_id: int, mode: str, status_msg: Message):
    """users / users_owner — need get_messages to check sender."""
    _RUNNING[chat_id] = True
    _CANCEL[chat_id] = False
    deleted = kept = failed = scanned = 0
    bot_id = None
    try:
        bot_id = (await client.get_me()).id
    except Exception:
        pass
    owner_id = await _get_owner_id(client, chat_id)

    top_id = int(getattr(status_msg, "id", 0) or 0)
    if top_id <= 1:
        try:
            await status_msg.edit_text("❌ Status id missing. Dubara /cleangroup.")
        except Exception:
            pass
        _RUNNING[chat_id] = False
        return

    lowest = max(1, top_id - MAX_SCAN)
    current = top_id - 1
    pending = []

    try:
        while current >= lowest:
            if _CANCEL.get(chat_id):
                break

            ids = []
            for _ in range(BATCH_SIZE):
                if current < lowest:
                    break
                ids.append(current)
                current -= 1
            if not ids:
                break

            try:
                msgs = await client.get_messages(chat_id, ids)
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                try:
                    msgs = await client.get_messages(chat_id, ids)
                except Exception:
                    continue
            except Exception as e:
                logger.warning("get_messages: %s", e)
                await asyncio.sleep(0.2)
                continue

            if not isinstance(msgs, list):
                msgs = [msgs]

            for msg in msgs:
                if msg is None or getattr(msg, "empty", False):
                    continue
                if msg.id == top_id:
                    continue

                scanned += 1
                uid = msg.from_user.id if msg.from_user else None

                keep = False
                if bot_id and uid == bot_id:
                    keep = True
                elif mode == "users" and owner_id and uid == owner_id:
                    keep = True

                if keep:
                    kept += 1
                else:
                    pending.append(msg.id)

            if len(pending) >= BATCH_SIZE:
                d, f = await _delete_ids(client, chat_id, pending)
                deleted += d
                failed += f
                pending = []
                try:
                    await status_msg.edit_text(
                        f"🧹 Cleaning...\nMode: {_label(mode)}\n"
                        f"Scanned: {scanned}\nDeleted: {deleted}\n"
                        f"Kept: {kept}\nFailed: {failed}"
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.2)

        if pending and not _CANCEL.get(chat_id):
            d, f = await _delete_ids(client, chat_id, pending)
            deleted += d
            failed += f

        final = (
            f"⏹ Cancelled\nScanned: {scanned}\nDeleted: {deleted}\nKept: {kept}\nFailed: {failed}"
            if _CANCEL.get(chat_id)
            else (
                f"✅ Done\nMode: {_label(mode)}\n"
                f"Scanned: {scanned}\nDeleted: {deleted}\n"
                f"Kept: {kept}\nFailed: {failed}\n"
                f"(last ~{MAX_SCAN} ids)"
            )
        )
        try:
            await status_msg.edit_text(final)
        except Exception:
            try:
                await client.send_message(chat_id, final)
            except Exception:
                pass
    except Exception as e:
        logger.exception("run_clean_filtered: %s", e)
        try:
            await status_msg.edit_text(f"❌ Error: {type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        _RUNNING[chat_id] = False
        _CANCEL[chat_id] = False


async def _run_clean(client: Client, chat_id: int, mode: str, status_msg: Message):
    if mode == "all":
        await _run_clean_all(client, chat_id, status_msg)
    else:
        await _run_clean_filtered(client, chat_id, mode, status_msg)


def register_cleangroup_command(bot: Client):
    @bot.on_message(
        filters.command(["cleangroup", "purgeall", "cleangrp"]) & filters.group,
        group=-3,
    )
    async def cleangroup_cmd(client: Client, message: Message):
        logger.info(
            "cleangroup cmd chat=%s user=%s",
            getattr(message.chat, "id", None),
            getattr(message.from_user, "id", None),
        )
        try:
            if not message.from_user or not message.chat:
                return
            chat_id = message.chat.id
            uid = message.from_user.id

            if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
                await message.reply_text("Ye command sirf group me chalega.")
                return

            if not await _is_admin(client, chat_id, uid):
                await message.reply_text("⛔ Sirf admin/owner use kar sakte hain.")
                return

            if _RUNNING.get(chat_id):
                await message.reply_text("⏳ Clean pehle se chal raha hai.")
                return

            if not await _bot_can_delete(client, chat_id):
                await message.reply_text(
                    "❌ Bot ko Admin + Delete messages permission do."
                )
                return

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("👤 Only Users", callback_data=f"cg_users:{uid}")],
                [InlineKeyboardButton("👤+👑 Users + Owner", callback_data=f"cg_uo:{uid}")],
                [InlineKeyboardButton("🗑 Delete ALL", callback_data=f"cg_all:{uid}")],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cg_no:{uid}")],
            ])
            await message.reply_text(
                "🧹 Clean Group\n\n"
                "Kitne messages delete karein?\n\n"
                "👤 Only Users — members only (owner+bot safe)\n"
                "👤+👑 Users + Owner — members+owner (bot safe)\n"
                "🗑 Delete ALL — sab (bot ke bhi)\n"
                "❌ Cancel\n\n"
                f"ℹ️ Last ~{MAX_SCAN} message ids scan/delete.",
                reply_markup=kb,
            )
            try:
                await message.delete()
            except Exception:
                pass
        except Exception as e:
            logger.exception("cleangroup_cmd: %s", e)
            try:
                await message.reply_text(f"❌ Error: {type(e).__name__}: {e}")
            except Exception:
                pass

    @bot.on_callback_query(group=-15)
    async def cleangroup_cb_router(client: Client, query: CallbackQuery):
        data = query.data or ""
        if not data.startswith("cg_"):
            return

        logger.info("cleangroup callback data=%s", data)
        try:
            if ":" in data:
                action, owner_s = data.split(":", 1)
            else:
                action, owner_s = data, ""
            try:
                owner_id = int(owner_s) if owner_s else None
            except Exception:
                owner_id = None

            clicker = query.from_user.id if query.from_user else 0
            chat_id = query.message.chat.id if query.message else 0

            if not (clicker == owner_id or await _is_admin(client, chat_id, clicker)):
                await _safe_answer(query, "Only admin can use this.", show_alert=True)
                return

            if action == "cg_no":
                await _safe_answer(query, "Cancelled")
                try:
                    await query.message.edit_text("❌ Cancelled. Kuch delete nahi hua.")
                except Exception:
                    pass
                return

            if action == "cg_stop":
                _CANCEL[chat_id] = True
                await _safe_answer(query, "Cancelling...")
                try:
                    await query.message.edit_text("⏹ Cancelling clean...")
                except Exception:
                    pass
                return

            mode = {
                "cg_users": "users",
                "cg_uo": "users_owner",
                "cg_all": "all",
            }.get(action)
            if not mode:
                await _safe_answer(query, "Unknown action")
                return

            if _RUNNING.get(chat_id):
                await _safe_answer(query, "Already running", show_alert=True)
                return

            if not await _bot_can_delete(client, chat_id):
                await _safe_answer(
                    query,
                    "Bot needs Delete messages permission",
                    show_alert=True,
                )
                return

            await _safe_answer(query, "Starting clean...")
            try:
                await query.message.edit_text(
                    f"🧹 Cleaning...\nMode: {_label(mode)}\nStarting...",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⏹ Cancel Clean", callback_data=f"cg_stop:{clicker}")]
                    ]),
                )
            except Exception as e:
                logger.warning("edit status: %s", e)

            asyncio.create_task(_run_clean(client, chat_id, mode, query.message))
        except Exception as e:
            logger.exception("cleangroup_cb: %s", e)
            await _safe_answer(query, f"Error: {type(e).__name__}", show_alert=True)

    logger.info("/cleangroup registered (all=direct-id-delete, filtered=get_messages)")
