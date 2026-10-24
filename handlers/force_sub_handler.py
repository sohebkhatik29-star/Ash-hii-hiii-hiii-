# ==============================================================================
# Force-Sub multi-channel system (max 7)
# ==============================================================================

import asyncio
import logging
from typing import List

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from configs import Config
from handlers.database import db

try:
    from handlers import force_sub_db  # noqa: F401
except Exception:
    pass

logger = logging.getLogger("ForceSub")
MAX_FORCE_CHANNELS = 7
DEFAULT_FORCE_MSG = (
    "👋 Hello <b>{name}</b>\n\n"
    "Aapne mera channel join nahi kiya hai.\n"
    "Pehle join karo, fir aap group me search / use kar sakte ho."
)
_FS_PENDING: dict = {}


def _status_is_admin(status) -> bool:
    s = str(getattr(status, "value", status) or status).lower()
    return s in (
        "administrator", "owner", "creator",
        "chatmemberstatus.administrator", "chatmemberstatus.owner",
        "chatmemberstatus.creator",
    )


async def _is_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        if user_id in (Config.ADMINS or []):
            return True
    except Exception:
        pass
    try:
        m = await client.get_chat_member(chat_id, user_id)
        if _status_is_admin(m.status):
            return True
        if getattr(m, "privileges", None) is not None:
            return True
    except Exception as e:
        logger.warning(f"admin check: {e}")
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
        st = str(getattr(member.status, "value", member.status) or member.status).lower()
        if _status_is_admin(member.status) or st in (
            "member", "restricted",
            "chatmemberstatus.member", "chatmemberstatus.restricted",
        ):
            return "ok"
        if st in ("banned", "kicked", "chatmemberstatus.banned"):
            return "kicked"
        return "not_joined"
    except UserNotParticipant:
        return "not_joined"
    except Exception as e:
        err = str(e).lower()
        if "not a participant" in err or "user_not_participant" in err:
            return "not_joined"
        return "error"


async def get_missing_channels(bot: Client, user_id: int) -> List[dict]:
    try:
        channels = await db.list_force_channels()
    except Exception as e:
        logger.warning(f"list_force_channels: {e}")
        channels = []
    missing = []
    for ch in channels:
        status = await check_user_in_channel(bot, int(ch["channel_id"]), user_id)
        if status in ("not_joined", "kicked"):
            missing.append(ch)
    return missing


async def build_force_sub_prompt(bot, user_name, missing, pending_query=""):
    try:
        custom = await db.get_force_sub_message()
    except Exception:
        custom = ""
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
        label = f"{'\ud83d\udce9 Request: ' if mode == 'request' else '\ud83d\udce2 Join: '}{title}"
        if url:
            buttons.append([InlineKeyboardButton(label, url=url)])
        else:
            buttons.append([InlineKeyboardButton(f"\u26a0\ufe0f {title}", callback_data="fs_noop")])
    cb = f"fs_refresh:{pending_query[:40]}" if pending_query else "fs_refresh"
    buttons.append([InlineKeyboardButton("\u2705 Joined / Try Again", callback_data=cb)])
    return text, InlineKeyboardMarkup(buttons)


async def enforce_force_sub_in_group(bot, message, query=""):
    if not message.from_user:
        return False
    user_id = message.from_user.id
    try:
        if user_id in (Config.ADMINS or []):
            return False
    except Exception:
        pass
    try:
        mem = await bot.get_chat_member(message.chat.id, user_id)
        if _status_is_admin(mem.status):
            return False
    except Exception:
        pass
    try:
        channels = await db.list_force_channels()
    except Exception as e:
        logger.warning(f"enforce list: {e}")
        channels = []
    if not channels:
        return False
    missing = await get_missing_channels(bot, user_id)
    if not missing:
        return False
    name = message.from_user.first_name or "User"
    try:
        text, markup = await build_force_sub_prompt(
            bot, name, missing, pending_query=query or (message.text or "")
        )
    except Exception as e:
        logger.warning(f"build force prompt: {e}")
        text = DEFAULT_FORCE_MSG.format(name=name)
        markup = None
    prompt = None
    try:
        prompt = await bot.send_message(
            message.chat.id, text, reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as e:
        logger.warning(f"force sub send: {e}")
        try:
            prompt = await message.reply_text(
                text, reply_markup=markup, quote=True, disable_web_page_preview=True
            )
        except Exception as e2:
            logger.warning(f"force sub reply: {e2}")
    try:
        await message.delete()
    except Exception as e:
        logger.debug(f"force-sub delete user msg: {e}")
    if prompt is not None:
        async def _later():
            await asyncio.sleep(300)
            try:
                await bot.delete_messages(message.chat.id, [prompt.id])
            except Exception:
                pass
        asyncio.create_task(_later())
    return True


async def handle_force_sub(bot: Client, cmd: Message) -> int:
    try:
        if not cmd.from_user:
            return 200
        uid = cmd.from_user.id
        if uid in (Config.ADMINS or []):
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
                    chat_id=cmd.chat.id, text=text, reply_markup=markup, disable_web_page_preview=True
                )
            except Exception as e:
                logger.warning(f"handle_force_sub prompt: {e}")
            return 400
        return 200
    except Exception as e:
        logger.warning(f"handle_force_sub error (allowing user): {e}")
        return 200


async def handle_fs_callback(client: Client, query: CallbackQuery):
    data = query.data or ""
    if data.startswith("fs_mode:"):
        if not query.from_user or not query.message:
            await query.answer()
            return
        chat_id = query.message.chat.id
        uid = query.from_user.id
        is_admin = await _is_group_admin(client, chat_id, uid)
        if not is_admin and uid not in (Config.ADMINS or []):
            await query.answer("Sirf admin use kar sakte hain.", show_alert=True)
            return
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Invalid", show_alert=True)
            return
        mode, channel_id = parts[1], int(parts[2])
        if mode == "cancel":
            _FS_PENDING.pop(channel_id, None)
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.message.edit_text("\u274c Cancelled.")
                except Exception:
                    pass
            await query.answer("Cancelled")
            return
        pending = _FS_PENDING.pop(channel_id, {}) or {}
        title = pending.get("title") or str(channel_id)
        username = pending.get("username") or ""
        try:
            doc = await db.add_force_channel(channel_id, title=title, username=username, mode=mode)
        except ValueError:
            await query.answer(f"Max {MAX_FORCE_CHANNELS} channels!", show_alert=True)
            try:
                await query.message.edit_text(f"\u274c Max {MAX_FORCE_CHANNELS} channels.")
            except Exception:
                pass
            return
        except Exception as e:
            logger.exception("add_force_channel failed")
            await query.answer(f"Error: {e}", show_alert=True)
            try:
                await query.message.edit_text(f"\u274c Error: <code>{e}</code>")
            except Exception:
                pass
            return
        try:
            await query.message.edit_text(
                f"\u2705 <b>Force-sub channel add ho gaya!</b>\n\n"
                f"\ud83d\udce2 <b>{doc.get('title')}</b>\n"
                f"ID: <code>{doc.get('channel_id')}</code>\n"
                f"Mode: <b>{doc.get('mode')}</b>\n\n"
                f"Ab users ko group me search se pehle ye channel join karna hoga."
            )
        except Exception:
            pass
        await query.answer("Added! \u2705")
        async def _clean():
            await asyncio.sleep(15)
            try:
                await client.delete_messages(chat_id, [query.message.id])
            except Exception:
                pass
        asyncio.create_task(_clean())
        return

    if data.startswith("fs_refresh"):
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
        pending_q = data.split(":", 1)[1].strip() if ":" in data else ""
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer("Verified! \u2705")
        if pending_q and query.message:
            try:
                tip = await client.send_message(
                    query.message.chat.id, f"\u2705 Verified. Dubara search: <b>{pending_q}</b>"
                )
                await asyncio.sleep(8)
                await tip.delete()
            except Exception:
                pass
        return

    if data == "fs_noop":
        await query.answer("Invite link nahi bana. Bot ko channel admin banao.", show_alert=True)
        return

    await query.answer()


def register_force_sub_handlers(bot: Client):

    @bot.on_message(filters.command(["add_force_chnl", "addforce", "add_force_channel"]) & filters.group)
    async def add_force_chnl_cmd(client: Client, message: Message):
        try:
            await _add_force_chnl_inner(client, message)
        except Exception as e:
            logger.exception("add_force_chnl crashed")
            try:
                await message.reply_text(f"\u274c Error: <code>{e}</code>", quote=True)
            except Exception:
                pass

    async def _add_force_chnl_inner(client: Client, message: Message):
        if not message.from_user:
            return
        if not await _is_group_admin(client, message.chat.id, message.from_user.id):
            await message.reply_text(
                "\ud83d\udeab Sirf group <b>admin / owner</b> force-sub channel add kar sakte hain.",
                quote=True,
            )
            return

        channel_id = None
        title = ""
        username = ""
        extra_delete_ids = []
        replied = message.reply_to_message

        if replied:
            extra_delete_ids.append(replied.id)
            if replied.forward_from_chat:
                src = replied.forward_from_chat
                channel_id = src.id
                title = src.title or ""
                username = getattr(src, "username", None) or ""
            elif getattr(replied, "forward_origin", None) and getattr(replied.forward_origin, "chat", None):
                src = replied.forward_origin.chat
                channel_id = src.id
                title = getattr(src, "title", "") or ""
                username = getattr(src, "username", None) or ""
            if channel_id is None and getattr(replied, "sender_chat", None):
                src = replied.sender_chat
                channel_id = src.id
                title = getattr(src, "title", "") or ""
                username = getattr(src, "username", None) or ""

        if channel_id is None:
            text = (message.text or message.caption or "").strip()
            parts = text.split(maxsplit=1)
            if len(parts) >= 2:
                raw = parts[1].strip()
                for pfx in ("https://t.me/", "http://t.me/", "t.me/", "@"):
                    if raw.lower().startswith(pfx):
                        raw = raw[len(pfx):]
                        break
                raw = raw.split()[0].split("?")[0].strip("/")
                if raw.startswith("c/"):
                    num = raw[2:].split("/")[0]
                    if num.isdigit():
                        channel_id = int("-100" + num)
                elif raw.lstrip("-").isdigit():
                    channel_id = int(raw)
                else:
                    uname = raw.split("/")[0]
                    try:
                        chat = await client.get_chat(uname)
                        channel_id = chat.id
                        title = chat.title or title
                        username = getattr(chat, "username", None) or username
                    except Exception as e:
                        await message.reply_text(
                            f"\u274c Channel nahi mila: <code>{e}</code>\n\n"
                            "Best way: channel se message <b>forward</b> \u2192 reply <code>/add_force_chnl</code>",
                            quote=True,
                        )
                        return

        if channel_id is None:
            await message.reply_text(
                "\ud83d\udce2 <b>Usage:</b>\n"
                "\u2022 Channel message <b>forward</b> \u2192 reply <code>/add_force_chnl</code>\n"
                "\u2022 Ya <code>/add_force_chnl -100xxxxxxxxxx</code>\n"
                "\u2022 Ya <code>/add_force_chnl @channelusername</code>",
                quote=True,
            )
            return

        try:
            me = await client.get_me()
            member = await client.get_chat_member(channel_id, me.id)
            if not _status_is_admin(member.status):
                await message.reply_text(
                    "\u274c <b>Is channel me bot admin nahi hai.</b>\nPehle bot ko channel me <b>Admin</b> banao.",
                    quote=True,
                )
                return
        except Exception as e:
            await message.reply_text(
                f"\u274c Bot is channel me admin nahi / access nahi.\n<code>{e}</code>",
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

        try:
            count = await db.count_force_channels()
            existing = await db.list_force_channels()
        except Exception as e:
            await message.reply_text(f"\u274c DB error: <code>{e}</code>", quote=True)
            return

        exists = any(int(c.get("channel_id", 0)) == int(channel_id) for c in existing)
        if not exists and count >= MAX_FORCE_CHANNELS:
            await message.reply_text(
                f"\u274c Max <b>{MAX_FORCE_CHANNELS}</b> channels. /del_force_chnl se hatao.",
                quote=True,
            )
            return

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("\ud83d\udd39 Normal (direct join)", callback_data=f"fs_mode:normal:{channel_id}")],
            [InlineKeyboardButton("\ud83d\udce9 Request (join request)", callback_data=f"fs_mode:request:{channel_id}")],
            [InlineKeyboardButton("\u274c Cancel", callback_data=f"fs_mode:cancel:{channel_id}")],
        ])
        prompt = await message.reply_text(
            f"\u2705 Bot is channel me <b>admin</b> hai.\n\n"
            f"\ud83d\udce2 <b>{title}</b>\nID: <code>{channel_id}</code>\n\n"
            f"<b>Mode choose karo:</b>\n"
            f"\u2022 <b>Normal</b> \u2014 user seedha join kare\n"
            f"\u2022 <b>Request</b> \u2014 user join-request bheje",
            reply_markup=markup,
            quote=True,
        )
        _FS_PENDING[int(channel_id)] = {
            "title": title,
            "username": username or "",
            "chat_id": message.chat.id,
            "delete_ids": [message.id] + extra_delete_ids,
            "prompt_id": prompt.id,
        }
        try:
            await client.delete_messages(message.chat.id, [message.id] + extra_delete_ids)
        except Exception:
            try:
                await message.delete()
            except Exception:
                pass

    @bot.on_message(filters.command(["del_force_chnl", "delforce", "del_force_channel"]) & filters.group)
    async def del_force_chnl_cmd(client: Client, message: Message):
        if not message.from_user:
            return
        if not await _is_group_admin(client, message.chat.id, message.from_user.id):
            await message.reply_text("\ud83d\udeab Sirf admin use kar sakte hain.", quote=True)
            return
        channel_id = None
        del_ids = [message.id]
        replied = message.reply_to_message
        if replied:
            del_ids.append(replied.id)
            if replied.forward_from_chat:
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
                "\ud83d\uddd1 Usage: <code>/del_force_chnl -100xxx</code> ya forward + reply",
                quote=True,
            )
            return
        try:
            ok = await db.remove_force_channel(channel_id)
        except Exception as e:
            await message.reply_text(f"\u274c Error: <code>{e}</code>", quote=True)
            return
        try:
            await client.delete_messages(message.chat.id, del_ids)
        except Exception:
            pass
        msg = await client.send_message(
            message.chat.id,
            f"\u2705 Channel <code>{channel_id}</code> force-sub se hata diya."
            if ok else f"\u274c Channel <code>{channel_id}</code> list me nahi mila.",
        )
        await asyncio.sleep(8)
        try:
            await msg.delete()
        except Exception:
            pass

    @bot.on_message(filters.command(["see_force_sub_chnl", "seeforce", "list_force", "force_channels"]) & filters.group)
    async def see_force_cmd(client: Client, message: Message):
        if not message.from_user:
            return
        if not await _is_group_admin(client, message.chat.id, message.from_user.id):
            await message.reply_text("\ud83d\udeab Sirf admin use kar sakte hain.", quote=True)
            return
        try:
            channels = await db.list_force_channels()
        except Exception as e:
            await message.reply_text(f"\u274c DB error: <code>{e}</code>", quote=True)
            return
        if not channels:
            await message.reply_text(
                "\ud83d\udded No force-sub channels.\n<code>/add_force_chnl</code> se add karo.",
                quote=True,
            )
            return
        lines = ["\ud83d\udce2 <b>Force-Sub Channels</b>\n"]
        for i, ch in enumerate(channels, 1):
            lines.append(
                f"<b>{i}. {ch.get('title') or 'Channel'}</b>"
                + (f" @{ch['username']}" if ch.get("username") else "")
                + f"\n   ID: <code>{ch.get('channel_id')}</code>\n"
                + f"   Mode: <b>{ch.get('mode') or 'normal'}</b>\n"
            )
        lines.append(f"\nTotal: <b>{len(channels)}</b> / {MAX_FORCE_CHANNELS}")
        await message.reply_text("\n".join(lines), quote=True)

    @bot.on_message(filters.command(["set_force_sub_message", "seth_force_sub_message", "set_force_msg"]) & filters.group)
    async def set_force_msg_cmd(client: Client, message: Message):
        if not message.from_user:
            return
        if not await _is_group_admin(client, message.chat.id, message.from_user.id):
            await message.reply_text("\ud83d\udeab Sirf admin use kar sakte hain.", quote=True)
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text(
                "\u270f\ufe0f <code>/set_force_sub_message Hello {name} please join</code>",
                quote=True,
            )
            return
        await db.set_force_sub_message(parts[1].strip())
        await message.reply_text(f"\u2705 Message set:\n\n{parts[1].strip()}", quote=True)

    @bot.on_message(filters.command(["del_force_sub_message", "del_force_msg"]) & filters.group)
    async def del_force_msg_cmd(client: Client, message: Message):
        if not message.from_user:
            return
        if not await _is_group_admin(client, message.chat.id, message.from_user.id):
            await message.reply_text("\ud83d\udeab Sirf admin use kar sakte hain.", quote=True)
            return
        await db.clear_force_sub_message()
        await message.reply_text("\u2705 Custom message deleted. Default use hoga.", quote=True)
