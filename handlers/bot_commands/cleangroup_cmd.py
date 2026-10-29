# ==============================================================================
# handlers/bot_commands/cleangroup_cmd.py
# Command: /cleangroup  (alias: /purgeall)
# Group me command → buttons:
#   Only Users | Users + Owner | Delete ALL | Cancel
# Sirf admin/owner use kar sakte hain. Bot ko Delete messages permission chahiye.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, QueryIdInvalid, MessageDeleteForbidden, ChatAdminRequired

from configs import Config

logger = logging.getLogger("CleanGroupCmd")

# chat_id -> cancel flag for running clean job
_CANCEL: dict = {}
_RUNNING: dict = {}


async def _safe_answer(query: CallbackQuery, text: str = "", show_alert: bool = False):
    try:
        await query.answer(text, show_alert=show_alert)
    except QueryIdInvalid:
        pass
    except Exception:
        pass


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
        logger.debug("cleangroup auth: %s", e)
    return False


async def _get_chat_owner_id(client: Client, chat_id: int):
    try:
        async for m in client.get_chat_members(chat_id, filter="administrators"):
            st = m.status
            st_s = str(getattr(st, "value", st) or st).lower()
            if "owner" in st_s or "creator" in st_s:
                if m.user:
                    return m.user.id
    except Exception as e:
        logger.debug("get owner: %s", e)
    return None


async def _bot_can_delete(client: Client, chat_id: int) -> bool:
    try:
        me = await client.get_chat_member(chat_id, "me")
        st = me.status
        st_s = str(getattr(st, "value", st) or st).lower()
        if "owner" in st_s or "creator" in st_s:
            return True
        if "administrator" in st_s:
            priv = getattr(me, "privileges", None)
            if priv is not None:
                return bool(getattr(priv, "can_delete_messages", False))
            return True
    except Exception as e:
        logger.warning("bot_can_delete: %s", e)
    return False


def _mode_label(mode: str) -> str:
    return {
        "users": "Only Users",
        "users_owner": "Users + Owner",
        "all": "Delete ALL (Bot+User+Owner)",
    }.get(mode, mode)


async def _run_clean(client: Client, chat_id: int, mode: str, status_msg: Message, starter_id: int):
    """Scan history and delete messages per mode."""
    _RUNNING[chat_id] = True
    _CANCEL[chat_id] = False
    deleted = 0
    skipped = 0
    batch: list = []
    bot_id = None
    try:
        me = await client.get_me()
        bot_id = me.id
    except Exception:
        pass

    owner_id = None
    if mode in ("users", "users_owner"):
        owner_id = await _get_chat_owner_id(client, chat_id)

    try:
        async for msg in client.get_chat_history(chat_id, limit=0):
            if _CANCEL.get(chat_id):
                break

            # never delete the status message we are editing
            if status_msg and msg.id == status_msg.id:
                skipped += 1
                continue

            should_delete = False
            uid = msg.from_user.id if msg.from_user else None

            if mode == "all":
                should_delete = True
            elif mode == "users_owner":
                # keep only bot messages
                if bot_id and uid == bot_id:
                    skipped += 1
                else:
                    should_delete = True
            elif mode == "users":
                # keep bot + owner
                if bot_id and uid == bot_id:
                    skipped += 1
                elif owner_id and uid == owner_id:
                    skipped += 1
                else:
                    should_delete = True

            if should_delete:
                batch.append(msg.id)

            if len(batch) >= 100:
                try:
                    await client.delete_messages(chat_id, batch)
                    deleted += len(batch)
                except FloodWait as e:
                    await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                    try:
                        await client.delete_messages(chat_id, batch)
                        deleted += len(batch)
                    except Exception as e2:
                        logger.warning("batch delete: %s", e2)
                        skipped += len(batch)
                except (MessageDeleteForbidden, ChatAdminRequired) as e:
                    logger.warning("delete forbidden: %s", e)
                    skipped += len(batch)
                except Exception as e:
                    logger.warning("delete batch: %s", e)
                    skipped += len(batch)
                batch = []

                # progress every ~100
                try:
                    await status_msg.edit_text(
                        f"🧹 Cleaning group...\n"
                        f"Mode: {_mode_label(mode)}\n\n"
                        f"Deleted: {deleted}\n"
                        f"Skipped: {skipped}\n"
                        f"Please wait..."
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.35)

        # leftover batch
        if batch and not _CANCEL.get(chat_id):
            try:
                await client.delete_messages(chat_id, batch)
                deleted += len(batch)
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                try:
                    await client.delete_messages(chat_id, batch)
                    deleted += len(batch)
                except Exception:
                    skipped += len(batch)
            except Exception as e:
                logger.warning("final batch: %s", e)
                skipped += len(batch)

        if _CANCEL.get(chat_id):
            text = (
                f"⏹ Cancelled\n\n"
                f"Deleted: {deleted}\n"
                f"Skipped: {skipped}"
            )
        else:
            text = (
                f"✅ Done\n\n"
                f"Mode: {_mode_label(mode)}\n"
                f"Deleted: {deleted}\n"
                f"Skipped: {skipped}"
            )
        try:
            await status_msg.edit_text(text)
        except Exception:
            try:
                await client.send_message(chat_id, text)
            except Exception:
                pass
    except Exception as e:
        logger.exception("run_clean: %s", e)
        try:
            await status_msg.edit_text(f"❌ Error: {type(e).__name__}: {e}")
        except Exception:
            pass
    finally:
        _RUNNING[chat_id] = False
        _CANCEL[chat_id] = False


def register_cleangroup_command(bot: Client):
    @bot.on_message(
        filters.command(["cleangroup", "purgeall"]) & filters.group,
        group=0,
    )
    async def cleangroup_cmd(client: Client, message: Message):
        if not message.from_user:
            return
        chat = message.chat
        if not chat or chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
            return

        if not await _is_authorized_uid(client, chat.id, message.from_user.id):
            try:
                await message.reply_text("⛔ Only admin/owner can use this command.")
            except Exception:
                pass
            return

        if _RUNNING.get(chat.id):
            try:
                await message.reply_text("⏳ Clean already running in this group. Wait or cancel.")
            except Exception:
                pass
            return

        if not await _bot_can_delete(client, chat.id):
            try:
                await message.reply_text(
                    "❌ Bot needs Admin + Delete messages permission in this group."
                )
            except Exception:
                pass
            return

        uid = message.from_user.id
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Only Users", callback_data=f"cg_users:{uid}")],
            [InlineKeyboardButton("👤+👑 Users + Owner", callback_data=f"cg_uo:{uid}")],
            [InlineKeyboardButton("🗑 Delete ALL (Bot+User+Owner)", callback_data=f"cg_all:{uid}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cg_no:{uid}")],
        ])
        text = (
            "🧹 Clean Group\n\n"
            "Kitne messages delete karne hain?\n\n"
            "👤 Only Users — members ke messages (owner + bot safe)\n"
            "👤+👑 Users + Owner — members + owner (sirf bot safe)\n"
            "🗑 Delete ALL — sab kuch delete (bot ke bhi)\n"
            "❌ Cancel — kuch mat karo"
        )
        try:
            await message.reply_text(text, reply_markup=kb)
        except Exception as e:
            logger.error("cleangroup prompt: %s", e)

        # delete the /cleangroup command message itself (optional cleanup)
        try:
            await message.delete()
        except Exception:
            pass

    @bot.on_callback_query(filters.regex(r"^cg_(users|uo|all|no):"), group=-5)
    async def cleangroup_callback(client: Client, query: CallbackQuery):
        try:
            data = query.data or ""
            parts = data.split(":", 1)
            if len(parts) != 2:
                return
            action, owner_s = parts[0], parts[1]
            try:
                owner_id = int(owner_s)
            except Exception:
                owner_id = None

            clicker = query.from_user.id if query.from_user else 0
            chat_id = query.message.chat.id if query.message else 0

            allowed = False
            if owner_id and clicker == owner_id:
                allowed = True
            if not allowed:
                allowed = await _is_authorized_uid(client, chat_id, clicker)
            if not allowed:
                await _safe_answer(query, "Only admin can confirm.", show_alert=True)
                return

            if action == "cg_no":
                await _safe_answer(query, "Cancelled")
                try:
                    await query.message.edit_text("❌ Cancelled. Kuch delete nahi hua.")
                except Exception:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                return

            mode_map = {
                "cg_users": "users",
                "cg_uo": "users_owner",
                "cg_all": "all",
            }
            mode = mode_map.get(action)
            if not mode:
                return

            if _RUNNING.get(chat_id):
                await _safe_answer(query, "Already running", show_alert=True)
                return

            if not await _bot_can_delete(client, chat_id):
                await _safe_answer(query, "Bot needs Delete permission", show_alert=True)
                return

            await _safe_answer(query, "Starting clean...")
            try:
                await query.message.edit_text(
                    f"🧹 Cleaning group...\n"
                    f"Mode: {_mode_label(mode)}\n\n"
                    f"Deleted: 0\n"
                    f"Skipped: 0\n"
                    f"Please wait...",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⏹ Cancel Clean", callback_data=f"cg_stop:{clicker}")]
                    ]),
                )
            except Exception:
                pass

            asyncio.create_task(
                _run_clean(client, chat_id, mode, query.message, clicker)
            )

        except Exception as e:
            logger.exception("cleangroup_callback: %s", e)
            await _safe_answer(query, "Error occurred", show_alert=True)

    @bot.on_callback_query(filters.regex(r"^cg_stop:"), group=-5)
    async def cleangroup_stop(client: Client, query: CallbackQuery):
        try:
            chat_id = query.message.chat.id if query.message else 0
            clicker = query.from_user.id if query.from_user else 0
            if not await _is_authorized_uid(client, chat_id, clicker):
                await _safe_answer(query, "Only admin", show_alert=True)
                return
            _CANCEL[chat_id] = True
            await _safe_answer(query, "Cancelling...")
            try:
                await query.message.edit_text("⏹ Cancelling clean... please wait.")
            except Exception:
                pass
        except Exception as e:
            logger.exception("cleangroup_stop: %s", e)

    logger.info("/cleangroup registered (users / users+owner / all / cancel)")
