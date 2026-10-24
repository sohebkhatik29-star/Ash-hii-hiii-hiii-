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
try:
    from handlers import force_sub_db
except Exception:
    pass

logger = logging.getLogger("ForceSub")

MAX_FORCE_CHANNELS = 7

DEFAULT_FORCE_MSG = (
    "👋 Hello <b>{name}</b>\n\n"
    "Aapne mera channel join nahi kiya hai.\n"
    "Pehle join karo, fir aap group me search / use kar sakte ho."
)


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
            return await bot.create_chat_invite_link(chat_id=chat_id, creates_join_request=True)
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
        return "error"


async def get_missing_channels(bot: Client, user_id: int) -> List[dict]:
    channels = await db.list_force_channels()
    missing = []
    for ch in channels:
        cid = int(ch["channel_id"])
        status = await check_user_in_channel(bot, cid, user_id)
        if status == "ok":
            continue
        if status in ("not_joined", "kicked"):
            missing.append(ch)
    return missing


async def build_force_sub_prompt(bot, user_name, missing, pending_query=""):
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
        invite = await get_invite_link(bot, cid, request=(mode == "request"))
        if invite and getattr(invite, "invite_link", None):
            url = invite.invite_link
        elif ch.get("username"):
            url = f"https://t.me/{ch['username'].lstrip('@')}"
        else:
            url = None
        label = f"{'📩 Request: ' if mode == 'request' else '📢 Join: '}{title}"
        if url:
            buttons.append([InlineKeyboardButton(label, url=url)])
        else:
            buttons.append([InlineKeyboardButton(f"⚠️ {title}", callback_data="fs_noop")])
    cb = f"fs_refresh:{pending_query[:40]}" if pending_query else "fs_refresh"
    buttons.append([InlineKeyboardButton("✅ Joined / Try Again", callback_data=cb)])
    return text, InlineKeyboardMarkup(buttons)


async def enforce_force_sub_in_group(bot, message, query=""):
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
            await asyncio.sleep(300)
            try:
                await bot.delete_messages(message.chat.id, [prompt.id])
            except Exception:
                pass

        asyncio.create_task(_later())
    except Exception as e:
        logger.warning(f"force sub prompt: {e}")
    return True


# ------------------------------------------------------------------
# Backward-compat for start_handler.py (private /start force-sub)
# ------------------------------------------------------------------
async def handle_force_sub(bot: Client, cmd: Message) -> int:
    """
    Legacy API used by start_handler.
    Returns 200 = allowed, 400 = blocked (prompt already sent).
    """
    try:
        if not cmd.from_user:
            return 200
        uid = cmd.from_user.id
        if uid in Config.ADMINS:
            return 200

        try:
            channels = await db.list_force_channels()
        except Exception:
            channels = []

        if channels:
            missing = await get_missing_channels(bot, uid)
            if not missing:
                return 200
            name = cmd.from_user.first_name or "User"
            text, markup = await build_force_sub_prompt(bot, name, missing)
            try:
                await bot.send_message(
                    chat_id=cmd.chat.id,
                    text=text,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning(f"handle_force_sub prompt: {e}")
            return 400

        if not Config.UPDATES_CHANNEL:
            return 200
        try:
            channel_chat_id = (
                int(Config.UPDATES_CHANNEL)
                if str(Config.UPDATES_CHANNEL).startswith("-100")
                else Config.UPDATES_CHANNEL
            )
        except Exception:
            channel_chat_id = Config.UPDATES_CHANNEL

        cid = channel_chat_id
        try:
            if str(cid).lstrip("-").isdigit():
                cid = int(cid)
        except Exception:
            pass

        status = await check_user_in_channel(bot, cid, uid)
        if status == "ok":
            return 200
        if status == "kicked":
            try:
                await bot.send_message(cmd.chat.id, "🚫 **Sorry, you are banned from using this bot.**")
            except Exception:
                pass
            return 400

        try:
            invite = await get_invite_link(bot, cid)
            channel_url = invite.invite_link if invite else (
                f"https://t.me/{Config.UPDATES_CHANNEL}"
                if not str(Config.UPDATES_CHANNEL).startswith("-")
                else f"https://t.me/{Config.BOT_USERNAME}"
            )
        except Exception:
            channel_url = f"https://t.me/{Config.BOT_USERNAME}"

        try:
            await bot.send_message(
                chat_id=cmd.chat.id,
                text=(
                    "📢 **Please Join Our Updates Channel To Use This Bot!**\n\n"
                    "Due to heavy load, only channel subscribers can access movies."
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join Updates Channel", url=channel_url)],
                    [InlineKeyboardButton("🔄 Refresh / Try Again", callback_data="refreshForceSub")],
                ]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning(f"legacy force sub: {e}")
            return 200
        return 400
    except Exception as e:
        logger.warning(f"handle_force_sub error (allowing user): {e}")
        return 200


def register_force_sub_handlers(bot: Client):

    @bot.on_message(filters.command(["add_force_chnl", "addforce", "add_force_channel"]) & filters.group)
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
                "📢 <b>Usage:</b>\n• Channel message forward + reply <code>/add_force_chnl</code>\n"
                "• Ya <code>/add_force_chnl -100xxx</code>\n• Ya <code>/add_force_chnl @username</code>",
                quote=True,
            )
            return
        try:
            me = await client.get_me()
            member = await client.get_chat_member(channel_id, me.id)
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                await message.reply_text(
                    "❌ <b>Is channel me bot admin nahi hai.</b>\nPehle bot ko channel me Admin banao.",
                    quote=True,
                )
                return
        except Exception as e:
            await message.reply_text(
                f"❌ Bot is channel me admin nahi / access nahi.\n<code>{e}</code>", quote=True
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
                f"❌ Max {MAX_FORCE_CHANNELS} channels. /del_force_chnl se hatao.", quote=True
            )
            return
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔹 Normal (direct join)", callback_data=f"fs_mode:normal:{channel_id}")],
            [InlineKeyboardButton("📩 Request (join request)", callback_data=f"fs_mode:request:{channel_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="fs_mode:cancel:0")],
        ])
        if not hasattr(db, "_fs_pending"):
            db._fs_pending = {}
        db._fs_pending[int(channel_id)] = {"title": title, "username": username or ""}
        await message.reply_text(
            f"✅ Bot admin hai.\n\n📢 <b>{title}</b>\nID: <code>{channel_id}</code>\n\n"
            f"<b>Mode choose karo:</b>",
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
        meta = getattr(db, "_fs_pending", {}).pop(channel_id, {}) or {}
        try:
            doc = await db.add_force_channel(
                channel_id,
                title=meta.get("title") or str(channel_id),
                username=meta.get("username") or "",
                mode=mode,
            )
        except ValueError:
            await query.message.edit_text(f"❌ Max {MAX_FORCE_CHANNELS} channels.")
            await query.answer()
            return
        await query.message.edit_text(
            f"✅ Force-sub add!\n\n📢 <b>{doc.get('title')}</b>\n"
            f"ID: <code>{doc.get('channel_id')}</code>\nMode: <b>{doc.get('mode')}</b>"
        )
        await query.answer("Added!")

    @bot.on_message(filters.command(["del_force_chnl", "delforce", "del_force_channel"]) & filters.group)
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
                        channel_id = (await client.get_chat(raw)).id
                    except Exception:
                        pass
        if channel_id is None:
            await message.reply_text(
                "🗑 Usage: <code>/del_force_chnl -100xxx</code> ya forward + reply", quote=True
            )
            return
        ok = await db.remove_force_channel(channel_id)
        await message.reply_text(
            f"✅ Removed <code>{channel_id}</code>" if ok else f"❌ Not found <code>{channel_id}</code>",
            quote=True,
        )

    @bot.on_message(
        filters.command(["see_force_sub_chnl", "seeforce", "list_force", "force_channels"])
        & filters.group
    )
    async def see_force_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        channels = await db.list_force_channels()
        if not channels:
            await message.reply_text(
                "📭 No force-sub channels. /add_force_chnl se add karo.", quote=True
            )
            return
        lines = ["📢 <b>Force-Sub Channels</b>\n"]
        for i, ch in enumerate(channels, 1):
            lines.append(
                f"<b>{i}. {ch.get('title') or 'Channel'}</b>"
                + (f" @{ch['username']}" if ch.get("username") else "")
                + f"\n   ID: <code>{ch.get('channel_id')}</code>\n   Mode: <b>{ch.get('mode') or 'normal'}</b>\n"
            )
        lines.append(f"\nTotal: <b>{len(channels)}</b> / {MAX_FORCE_CHANNELS}")
        await message.reply_text("\n".join(lines), quote=True)

    @bot.on_message(
        filters.command(["set_force_sub_message", "seth_force_sub_message", "set_force_msg"])
        & filters.group
    )
    async def set_force_msg_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text(
                "✏️ <code>/set_force_sub_message Hello {name} please join</code>", quote=True
            )
            return
        await db.set_force_sub_message(parts[1].strip())
        await message.reply_text(f"✅ Message set:\n\n{parts[1].strip()}", quote=True)

    @bot.on_message(filters.command(["del_force_sub_message", "del_force_msg"]) & filters.group)
    async def del_force_msg_cmd(client: Client, message: Message):
        if not await _is_group_admin(client, message):
            return
        await db.clear_force_sub_message()
        await message.reply_text("✅ Custom message deleted. Default use hoga.", quote=True)

    @bot.on_callback_query(filters.regex(r"^fs_refresh"))
    async def fs_refresh_callback(client: Client, query: CallbackQuery):
        user = query.from_user
        if not user:
            await query.answer()
            return
        missing = await get_missing_channels(client, user.id)
        if missing:
            await query.answer("Abhi join nahi kiya! Pehle join/request karo.", show_alert=True)
            text, markup = await build_force_sub_prompt(client, user.first_name or "User", missing)
            try:
                await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            except Exception:
                pass
            return
        pending_q = query.data.split(":", 1)[1].strip() if ":" in (query.data or "") else ""
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("Verified! ✅")
        if pending_q and query.message:
            try:
                tip = await client.send_message(
                    query.message.chat.id,
                    f"✅ Verified. Dubara search: <b>{pending_q}</b>",
                )
                await asyncio.sleep(8)
                await tip.delete()
            except Exception:
                pass

    @bot.on_callback_query(filters.regex(r"^fs_noop$"))
    async def fs_noop(client: Client, query: CallbackQuery):
        await query.answer("Invite link nahi bana. Bot ko channel admin banao.", show_alert=True)
