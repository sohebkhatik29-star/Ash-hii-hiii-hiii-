# ==============================================================================
# Force-Sub multi-channel system (max 7)
# Modes: normal (must join) | request (join-request button)
# ==============================================================================

import asyncio
import logging
from typing import List, Optional, Tuple

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.errors import (
    FloodWait,
    UserNotParticipant,
    ChatAdminRequired,
    ChannelPrivate,
    UserAlreadyParticipant,
)
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)

from configs import Config
from handlers.database import db

logger = logging.getLogger("ForceSub")

MAX_FORCE_CHANNELS = 7

DEFAULT_FORCE_MSG = (
    "👋 Hello <b>{name}</b>\n\n"
    "Aapne mera channel join nahi kiya hai.\n"
    "Pehle join karo, fir aap group me search / use kar sakte ho."
)


def _is_config_admin(user_id: int) -> bool:
    return user_id in Config.ADMINS


async def _is_group_admin(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    uid = message.from_user.id
    if uid in Config.ADMINS:
        return True
    try:
        m = await client.get_chat_member(message.chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception:
        return False


async def get_invite_link(bot: Client, chat_id: int, request: bool = False):
    try:
        if request:
            return await bot.create_chat_invite_link(
                chat_id=chat_id,
                creates_join_request=True,
            )
        return await bot.create_chat_invite_link(chat_id=chat_id)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await get_invite_link(bot, chat_id, request=request)
    except Exception as e:
        logger.warning(f"invite link {chat_id}: {e}")
        return None


async def check_user_in_channel(bot: Client, channel_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        status = member.status
        if status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            ChatMemberStatus.RESTRICTED,
        ):
            return "ok"
        if status == ChatMemberStatus.BANNED:
            return "kicked"
        if str(status).lower() in ("member", "administrator", "owner", "creator", "restricted"):
            return "ok"
        return "not_joined"
    except UserNotParticipant:
        return "not_joined"
    except Exception as e:
        err = str(e).lower()
        if "not a participant" in err or "user_not_participant" in err:
            return "not_joined"
        logger.debug(f"check_user_in_channel {channel_id}/{user_id}: {e}")
        return "error"


async def get_missing_channels(bot: Client, user_id: int) -> List[dict]:
    channels = await db.list_force_channels()
    missing = []
    for ch in channels:
        cid = int(ch["channel_id"])
        status = await check_user_in_channel(bot, cid, user_id)
        if status == "ok":
            continue
        if status == "kicked":
            ch = dict(ch)
            ch["_kicked"] = True
            missing.append(ch)
            continue
        if status == "not_joined":
            missing.append(ch)
    return missing


async def build_force_sub_prompt(
    bot: Client,
    user_name: str,
    missing: List[dict],
    pending_query: str = "",
) -> Tuple[str, InlineKeyboardMarkup]:
    custom = await db.get_force_sub_message()
    if custom:
        text = (
            custom.replace("{name}", user_name)
            .replace("{username}", user_name)
            .replace("{user}", user_name)
        )
    else:
        text = DEFAULT_FORCE_MSG.format(name=user_name)

    text += "\n\n📢 <b>Join these channel(s):</b>\n"
    buttons = []
    for ch in missing:
        cid = int(ch["channel_id"])
        title = ch.get("title") or ch.get("username") or str(cid)
        mode = ch.get("mode") or "normal"
        request = mode == "request"
        invite = await get_invite_link(bot, cid, request=request)
        if invite and getattr(invite, "invite_link", None):
            url = invite.invite_link
        elif ch.get("username"):
            url = f"https://t.me/{ch['username'].lstrip('@')}"
        else:
            url = None
        label = f"{'📩 Request: ' if request else '📢 Join: '}{title}"
        if url:
            buttons.append([InlineKeyboardButton(label, url=url)])
        else:
            buttons.append([InlineKeyboardButton(f"⚠️ {title} (link nahi mila)", callback_data="fs_noop")])

    cb = "fs_refresh"
    if pending_query:
        safe_q = pending_query[:40]
        cb = f"fs_refresh:{safe_q}"
    buttons.append([InlineKeyboardButton("✅ Joined / Try Again", callback_data=cb)])
    return text, InlineKeyboardMarkup(buttons)


async def enforce_force_sub_in_group(
    bot: Client,
    message: Message,
    query: str = "",
) -> bool:
    if not message.from_user:
        return False
    user_id = message.from_user.id
    if user_id in Config.ADMINS:
        return False
    try:
        mem = await bot.get_chat_member(message.chat.id, user_id)
        if mem.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return False
    except Exception:
        pass

    channels = await db.list_force_channels()
    if not channels:
        return False

    missing = await get_missing_channels(bot, user_id)
    if not missing:
        return False

    name = message.from_user.first_name or "User"
    text, markup = await build_force_sub_prompt(
        bot, name, missing, pending_query=query or (message.text or "")
    )
    try:
        prompt = await message.reply_text(
            text, reply_markup=markup, quote=True, disable_web_page_preview=True
        )

        async def _later():
            await asyncio.sleep(5 * 60)
            try:
                await bot.delete_messages(message.chat.id, [prompt.id])
            except Exception:
                pass

        asyncio.create_task(_later())
    except Exception as e:
        logger.warning(f"force sub prompt: {e}")
    return True


def register_force_sub_handlers(bot: Client):

    @bot.on_message(
        filters.command(["add_force_chnl", "addforce", "add_force_channel"]) & filters.group
    )
    async def add_force_chnl_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        channel_id = None
        title = ""
        username = ""

        replied = message.reply_to_message
        if replied and replied.forward_from_chat:
            src = replied.forward_from_chat
            channel_id = src.id
            title = src.title or ""
            username = getattr(src, "username", None) or ""

        if channel_id is None:
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) >= 2:
                raw = parts[1].strip().replace("https://t.me/", "").replace("t.me/", "").lstrip("@")
                if raw.lstrip("-").isdigit():
                    channel_id = int(raw)
                else:
                    try:
                        chat = await client.get_chat(raw)
                        channel_id = chat.id
                        title = chat.title or title
                        username = getattr(chat, "username", None) or username
                    except Exception as e:
                        await message.reply_text(f"❌ Channel nahi mila: <code>{e}</code>", quote=True)
                        return

        if channel_id is None:
            await message.reply_text(
                "📢 <b>Usage:</b>\n"
                "• Channel se message <b>forward</b> karke reply me <code>/add_force_chnl</code>\n"
                "• Ya <code>/add_force_chnl -100xxxxxxxxxx</code>\n"
                "• Ya <code>/add_force_chnl @channelusername</code>",
                quote=True,
            )
            return

        try:
            me = await client.get_me()
            member = await client.get_chat_member(channel_id, me.id)
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                await message.reply_text(
                    "❌ <b>Is channel me bot admin nahi hai.</b>\n"
                    "Pehle bot ko channel me <b>Admin</b> banao, phir dubara /add_force_chnl chalao.",
                    quote=True,
                )
                return
        except Exception as e:
            await message.reply_text(
                f"❌ <b>Is channel me bot admin nahi hai / access nahi hai.</b>\n"
                f"Pehle bot ko channel me Admin banao.\n\n<code>{e}</code>",
                quote=True,
            )
            return

        if not title:
            try:
                chat = await client.get_chat(channel_id)
                title = chat.title or str(channel_id)
                username = getattr(chat, "username", None) or username
            except Exception:
                title = str(channel_id)

        count = await db.count_force_channels()
        exists = any(
            int(c.get("channel_id", 0)) == int(channel_id)
            for c in await db.list_force_channels()
        )
        if not exists and count >= MAX_FORCE_CHANNELS:
            await message.reply_text(
                f"❌ Maximum <b>{MAX_FORCE_CHANNELS}</b> force-sub channels allowed.\n"
                f"Pehle kisi ko /del_force_chnl se hatao.",
                quote=True,
            )
            return

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔹 Normal (direct join)",
                        callback_data=f"fs_mode:normal:{channel_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📩 Request (join request)",
                        callback_data=f"fs_mode:request:{channel_id}",
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data="fs_mode:cancel:0")],
            ]
        )
        if not hasattr(db, "_fs_pending"):
            db._fs_pending = {}
        db._fs_pending[int(channel_id)] = {"title": title, "username": username or ""}

        await message.reply_text(
            f"✅ Bot is channel me <b>admin</b> hai.\n\n"
            f"📢 <b>{title}</b>\n"
            f"ID: <code>{channel_id}</code>\n\n"
            f"<b>Mode choose karo:</b>\n"
            f"• <b>Normal</b> — user seedha join kare\n"
            f"• <b>Request</b> — user join-request bheje",
            reply_markup=markup,
            quote=True,
        )
        try:
            await message.delete()
        except Exception:
            pass

    @bot.on_callback_query(filters.regex(r"^fs_mode:(normal|request|cancel):(-?\d+)$"))
    async def fs_mode_callback(client: Client, query: CallbackQuery):
        if not query.from_user:
            return
        allowed = query.from_user.id in Config.ADMINS
        if not allowed:
            try:
                m = await client.get_chat_member(query.message.chat.id, query.from_user.id)
                allowed = m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
            except Exception:
                allowed = False
        if not allowed:
            await query.answer("Sirf admin.", show_alert=True)
            return

        parts = query.data.split(":")
        mode, cid_s = parts[1], parts[2]
        if mode == "cancel":
            await query.message.edit_text("❌ Cancelled.")
            await query.answer()
            return
        channel_id = int(cid_s)
        meta = {}
        if hasattr(db, "_fs_pending"):
            meta = db._fs_pending.pop(channel_id, {}) or {}
        title = meta.get("title") or str(channel_id)
        username = meta.get("username") or ""
        try:
            doc = await db.add_force_channel(
                channel_id, title=title, username=username, mode=mode
            )
        except ValueError as e:
            if str(e) == "MAX_7":
                await query.message.edit_text(f"❌ Max {MAX_FORCE_CHANNELS} channels.")
                await query.answer()
                return
            raise
        await query.message.edit_text(
            f"✅ Force-sub channel add ho gaya!\n\n"
            f"📢 <b>{doc.get('title')}</b>\n"
            f"ID: <code>{doc.get('channel_id')}</code>\n"
            f"Mode: <b>{doc.get('mode')}</b>\n\n"
            f"Ab users ko group me search se pehle ye channel join karna hoga."
        )
        await query.answer("Added!")

    @bot.on_message(
        filters.command(["del_force_chnl", "delforce", "del_force_channel"]) & filters.group
    )
    async def del_force_chnl_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        channel_id = None
        replied = message.reply_to_message
        if replied and replied.forward_from_chat:
            channel_id = replied.forward_from_chat.id
        if channel_id is None:
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) >= 2:
                raw = parts[1].strip().lstrip("@")
                if raw.lstrip("-").isdigit():
                    channel_id = int(raw)
                else:
                    try:
                        chat = await client.get_chat(raw)
                        channel_id = chat.id
                    except Exception:
                        pass
        if channel_id is None:
            await message.reply_text(
                "🗑 <b>Usage:</b>\n"
                "• <code>/del_force_chnl -100xxxxxxxxxx</code>\n"
                "• Ya channel message forward karke reply me /del_force_chnl",
                quote=True,
            )
            return
        ok = await db.remove_force_channel(channel_id)
        if ok:
            await message.reply_text(
                f"✅ Channel <code>{channel_id}</code> force-sub se hata diya.", quote=True
            )
        else:
            await message.reply_text(
                f"❌ Channel <code>{channel_id}</code> list me nahi mila.", quote=True
            )

    @bot.on_message(
        filters.command(
            ["see_force_sub_chnl", "seeforce", "list_force", "force_channels"]
        )
        & filters.group
    )
    async def see_force_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        channels = await db.list_force_channels()
        if not channels:
            await message.reply_text(
                "📭 Koi force-sub channel set nahi hai.\n<code>/add_force_chnl</code> se add karo.",
                quote=True,
            )
            return
        lines = ["📢 <b>Force-Sub Channels</b>\n"]
        for i, ch in enumerate(channels, 1):
            title = ch.get("title") or "Channel"
            cid = ch.get("channel_id")
            mode = ch.get("mode") or "normal"
            uname = ch.get("username")
            uname_s = f" @{uname}" if uname else ""
            lines.append(
                f"<b>{i}. {title}</b>{uname_s}\n"
                f"   ID: <code>{cid}</code>\n"
                f"   Mode: <b>{mode}</b>\n"
            )
        lines.append(f"\nTotal: <b>{len(channels)}</b> / {MAX_FORCE_CHANNELS}")
        await message.reply_text("\n".join(lines), quote=True)

    @bot.on_message(
        filters.command(
            ["set_force_sub_message", "seth_force_sub_message", "set_force_msg"]
        )
        & filters.group
    )
    async def set_force_msg_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text(
                "✏️ <b>Usage:</b>\n"
                "<code>/set_force_sub_message Hello {name} please join channel</code>\n\n"
                "Placeholder: <code>{name}</code> = user ka naam",
                quote=True,
            )
            return
        await db.set_force_sub_message(parts[1].strip())
        await message.reply_text(
            f"✅ Force-sub message set:\n\n{parts[1].strip()}", quote=True
        )

    @bot.on_message(
        filters.command(["del_force_sub_message", "del_force_msg"]) & filters.group
    )
    async def del_force_msg_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        await db.clear_force_sub_message()
        await message.reply_text(
            "✅ Custom force-sub message delete.\nAb bot ka original message use hoga.",
            quote=True,
        )

    @bot.on_callback_query(filters.regex(r"^fs_refresh"))
    async def fs_refresh_callback(client: Client, query: CallbackQuery):
        user = query.from_user
        if not user:
            await query.answer()
            return
        missing = await get_missing_channels(client, user.id)
        if missing:
            await query.answer(
                "Abhi bhi channel join nahi kiya! Pehle join/request karo.",
                show_alert=True,
            )
            text, markup = await build_force_sub_prompt(
                client, user.first_name or "User", missing
            )
            try:
                await query.message.edit_text(
                    text, reply_markup=markup, disable_web_page_preview=True
                )
            except Exception:
                pass
            return

        pending_q = ""
        if ":" in (query.data or ""):
            pending_q = query.data.split(":", 1)[1].strip()
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_text("✅ Verified! Ab search kar sakte ho.")
            except Exception:
                pass
        await query.answer("Verified! ✅")

        if pending_q and query.message and query.message.chat:
            try:
                tip = await client.send_message(
                    query.message.chat.id,
                    f"✅ Join verified.\nAb dubara search karo: <b>{pending_q}</b>",
                )
                await asyncio.sleep(8)
                try:
                    await tip.delete()
                except Exception:
                    pass
            except Exception as e:
                logger.debug(f"fs refresh: {e}")

    @bot.on_callback_query(filters.regex(r"^fs_noop$"))
    async def fs_noop(client: Client, query: CallbackQuery):
        await query.answer(
            "Channel invite link nahi ban paya. Bot ko channel me admin banao.",
            show_alert=True,
        )
