# Force-Sub multi-channel system (max 7)

import asyncio
import logging
import re
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
    "Hello <b>{name}</b>\n\n"
    "Aapne mera channel join nahi kiya hai.\n"
    "Pehle join karo, fir aap group me search / use kar sakte ho."
)
_FS_PENDING: dict = {}
_PENDING_REQUESTS: dict = {}
_FS_PANEL_TTL = 300  # 5 minutes


async def _auto_delete_fs_panel(client: Client, chat_id: int, msg_id: int, delay: int = _FS_PANEL_TTL):
    """Delete force-sub panel if still present after delay."""
    try:
        await asyncio.sleep(delay)
        await client.delete_messages(chat_id, msg_id)
        logger.info("fs panel auto-deleted chat=%s msg=%s", chat_id, msg_id)
    except Exception as e:
        logger.debug("fs panel auto-delete: %s", e)


def _display_user(user) -> str:
    if not user:
        return "User"
    un = getattr(user, "username", None)
    if un:
        return f"@{_safe_text(un).lstrip('@')}"
    fn = _safe_text(getattr(user, "first_name", None) or "")
    return fn or "User"


def _safe_text(s) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return ""
    s = re.sub(r"[\ud800-\udfff]", "", s)
    try:
        s = s.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:
        s = "".join(ch for ch in s if ord(ch) < 0xD800 or ord(ch) > 0xDFFF)
    return s


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
        logger.warning("admin check: %s", e)
    return False


async def _safe_reply(message: Message, text: str, **kwargs):
    text = _safe_text(text)
    try:
        return await message.reply_text(text, **kwargs)
    except Exception as e:
        logger.warning("safe_reply: %s", e)
        return None


async def check_user_in_channel(bot: Client, channel_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        status = str(getattr(member.status, "value", member.status) or member.status).lower()
        if any(x in status for x in ("member", "administrator", "owner", "creator", "restricted")):
            return "joined"
        if "left" in status:
            return "not_joined"
        if "kicked" in status or "banned" in status:
            return "kicked"
        return "not_joined"
    except UserNotParticipant:
        return "not_joined"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return await check_user_in_channel(bot, channel_id, user_id)
    except Exception as e:
        logger.warning("check_user_in_channel %s: %s", channel_id, e)
        return "error"


async def has_pending_join_request(bot: Client, channel_id: int, user_id: int) -> bool:
    key = (int(channel_id), int(user_id))
    if key in _PENDING_REQUESTS:
        return True
    return False


async def get_invite_link(bot: Client, channel_id: int, request: bool = False):
    try:
        return await bot.create_chat_invite_link(
            channel_id,
            creates_join_request=bool(request),
        )
    except Exception as e:
        logger.warning("get_invite_link %s: %s", channel_id, e)
        return None


async def get_missing_channels(bot: Client, user_id: int) -> list:
    try:
        channels = await db.list_force_channels()
    except Exception:
        channels = []
    missing = []
    for ch in channels or []:
        try:
            cid = int(ch.get("channel_id"))
        except Exception:
            continue
        mode = (ch.get("mode") or "normal").lower()
        status = await check_user_in_channel(bot, cid, user_id)
        if mode == "request" and status in ("not_joined", "error"):
            if await has_pending_join_request(bot, cid, user_id):
                continue
            missing.append(ch)
            continue
        if status in ("not_joined", "kicked", "error"):
            missing.append(ch)
    return missing


async def build_force_sub_prompt(bot, user_name, missing, pending_query="", owner_id: int = 0):
    """Prompt buttons: channel name + Joined / Try Again. owner_id locked into callback."""
    user_name = _safe_text(user_name) or "User"
    try:
        custom = await db.get_force_sub_message()
    except Exception:
        custom = ""
    if custom:
        text = (
            _safe_text(custom)
            .replace("{name}", user_name)
            .replace("{username}", user_name)
            .replace("{user}", user_name)
        )
    else:
        text = DEFAULT_FORCE_MSG.format(name=user_name)
    text += "\n\nJoin these channel(s):\n"
    buttons = []
    for ch in missing:
        cid = int(ch["channel_id"])
        title = _safe_text(ch.get("title") or ch.get("username") or str(cid))
        if title.lstrip("-").isdigit() and ch.get("username"):
            title = str(ch["username"]).lstrip("@")
        mode = (ch.get("mode") or "normal").lower()
        invite = await get_invite_link(bot, cid, request=(mode == "request"))
        if invite and getattr(invite, "invite_link", None):
            url = invite.invite_link
        elif ch.get("username"):
            url = f"https://t.me/{str(ch['username']).lstrip('@')}"
        else:
            url = None
        label = title[:60] if title else str(cid)
        if url:
            buttons.append([InlineKeyboardButton(label, url=url)])
        else:
            buttons.append([InlineKeyboardButton(label, callback_data="fs_noop")])
    qpart = _safe_text(pending_query)[:28] if pending_query else ""
    oid = int(owner_id or 0)
    if oid and qpart:
        cb = f"fs_refresh:{oid}:{qpart}"
    elif oid:
        cb = f"fs_refresh:{oid}"
    elif qpart:
        cb = f"fs_refresh:0:{qpart}"
    else:
        cb = "fs_refresh"
    if len(cb) > 64:
        cb = cb[:64]
    buttons.append([InlineKeyboardButton("Joined / Try Again", callback_data=cb)])
    return _safe_text(text), InlineKeyboardMarkup(buttons)


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
    except Exception:
        channels = []
    if not channels:
        return False
    missing = await get_missing_channels(bot, user_id)
    if not missing:
        return False
    name = _display_user(message.from_user)
    text, markup = await build_force_sub_prompt(
        bot, name, missing, pending_query=query or "", owner_id=user_id
    )
    try:
        await message.delete()
    except Exception:
        pass
    pic = ""
    try:
        pic = await db.get_force_sub_pic()
    except Exception:
        pic = ""
    sent = None
    try:
        if pic:
            sent = await bot.send_photo(message.chat.id, pic, caption=text, reply_markup=markup)
        else:
            sent = await bot.send_message(
                message.chat.id, text, reply_markup=markup, disable_web_page_preview=True
            )
    except Exception as e:
        logger.warning("enforce prompt: %s", e)
    if sent is not None:
        try:
            asyncio.create_task(
                _auto_delete_fs_panel(bot, message.chat.id, sent.id, _FS_PANEL_TTL)
            )
        except Exception as e:
            logger.debug("fs schedule delete: %s", e)
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
            name = _display_user(cmd.from_user)
            text, markup = await build_force_sub_prompt(bot, name, missing, owner_id=uid)
            pic = ""
            try:
                pic = await db.get_force_sub_pic()
            except Exception:
                pic = ""
            sent = None
            try:
                if pic:
                    sent = await bot.send_photo(chat_id=cmd.chat.id, photo=pic, caption=text, reply_markup=markup)
                else:
                    sent = await bot.send_message(
                        chat_id=cmd.chat.id, text=text, reply_markup=markup, disable_web_page_preview=True
                    )
            except Exception as e:
                logger.warning("handle_force_sub prompt: %s", e)
            if sent is not None:
                try:
                    asyncio.create_task(
                        _auto_delete_fs_panel(bot, cmd.chat.id, sent.id, _FS_PANEL_TTL)
                    )
                except Exception:
                    pass
            return 400
        return 200
    except Exception as e:
        logger.error("handle_force_sub: %s", e)
        return 200


async def handle_fs_callback(client: Client, query: CallbackQuery):
    data = query.data or ""
    if data.startswith("fs_mode:"):
        if not query.from_user or not query.message:
            try:
                await query.answer()
            except Exception:
                pass
            return
        chat_id = query.message.chat.id
        uid = query.from_user.id
        is_admin = await _is_group_admin(client, chat_id, uid)
        if not is_admin and uid not in (Config.ADMINS or []):
            try:
                await query.answer("Sirf admin use kar sakte hain.", show_alert=True)
            except Exception:
                pass
            return
        parts = data.split(":")
        if len(parts) < 3:
            try:
                await query.answer("Invalid", show_alert=True)
            except Exception:
                pass
            return
        mode, channel_id = parts[1], int(parts[2])
        if mode == "cancel":
            _FS_PENDING.pop(channel_id, None)
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.message.edit_text("Cancelled.")
                except Exception:
                    pass
            try:
                await query.answer("Cancelled")
            except Exception:
                pass
            return
        pending = _FS_PENDING.pop(channel_id, {}) or {}
        title = _safe_text(pending.get("title") or str(channel_id))
        username = pending.get("username") or ""
        try:
            await db.add_force_channel(channel_id, title=title, username=username, mode=mode)
            await query.message.edit_text(
                f"\u2705 Force-sub channel add ho gaya.\n\n"
                f"Channel: <b>{title}</b>\nMode: <code>{mode}</code>\nID: <code>{channel_id}</code>"
            )
            await query.answer("Added!")
        except ValueError as ve:
            msg = "Max 7 channels" if str(ve) == "MAX_7" else str(ve)[:80]
            await query.answer(msg, show_alert=True)
        except Exception as e:
            logger.error("fs_mode add: %s", e)
            await query.answer("Error", show_alert=True)
        return

    if data.startswith("fs_refresh") or data in ("force_sub_joined", "force_sub_try", "fs_try", "fs_joined"):
        user = query.from_user
        if not user:
            try:
                await query.answer()
            except Exception:
                pass
            return

        owner_id = 0
        pending_q = ""
        if data.startswith("fs_refresh:"):
            rest = data.split(":", 1)[1]
            parts = rest.split(":", 1)
            if parts and parts[0].isdigit():
                owner_id = int(parts[0])
                pending_q = parts[1] if len(parts) > 1 else ""
            else:
                pending_q = rest

        clicker_id = int(user.id)

        if owner_id and clicker_id != owner_id:
            clicker_missing = await get_missing_channels(client, clicker_id)
            if clicker_missing:
                msg = (
                    "This join panel is not for you.\n\n"
                    "Only the user who received this message can press "
                    "Joined / Try Again.\n\n"
                    "If you still need to join the channel(s), please send a "
                    "message in the group first \u2014 you will get your own join panel."
                )
            else:
                msg = (
                    "This join panel is not for you.\n\n"
                    "You are already joined to the required channel(s). "
                    "Only the user who received this message can use this button.\n\n"
                    "If you want to search a movie, type the movie name in the group."
                )
            try:
                await query.answer(msg[:200], show_alert=True)
            except Exception:
                try:
                    await query.answer("This panel is not for you.", show_alert=True)
                except Exception:
                    pass
            return

        missing = await get_missing_channels(client, clicker_id)
        if not missing:
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.message.edit_text(" ")
                    await query.message.delete()
                except Exception:
                    pass
            try:
                await query.answer("Joined!")
            except Exception:
                pass
            uname = _display_user(user)
            welcome = (
                f"Welcome, {uname}!\n\n"
                f"You have successfully joined. You are now free to use the bot.\n"
                f"Please type the movie name in the group to search."
            )
            if pending_q:
                welcome += f"\n\nYou can search again for: <b>{_safe_text(pending_q)}</b>"
            try:
                tip = await client.send_message(query.message.chat.id, welcome)
                await asyncio.sleep(12)
                try:
                    await tip.delete()
                except Exception:
                    pass
            except Exception:
                pass
            return

        text, markup = await build_force_sub_prompt(
            client, _display_user(user), missing,
            pending_query=pending_q, owner_id=clicker_id,
        )
        try:
            await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
        try:
            await query.answer(
                "You have not joined the required channel(s) yet. Please join first.",
                show_alert=True,
            )
        except Exception:
            pass
        return

    if data.startswith("fs_mark_req:"):
        user = query.from_user
        if not user:
            try:
                await query.answer()
            except Exception:
                pass
            return
        try:
            cid = int(data.split(":", 1)[1])
        except Exception:
            try:
                await query.answer("Invalid channel", show_alert=True)
            except Exception:
                pass
            return
        uid = int(user.id)
        _PENDING_REQUESTS[(cid, uid)] = asyncio.get_event_loop().time()
        try:
            await query.answer(
                "\u2705 Request mark ho gaya. Ab 'Joined / Try Again' dabao.",
                show_alert=True,
            )
        except Exception:
            pass
        missing = await get_missing_channels(client, uid)
        if missing:
            text, markup = await build_force_sub_prompt(
                client, _display_user(user), missing, owner_id=uid
            )
            try:
                await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
            except Exception:
                pass
        else:
            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                tip = await client.send_message(
                    query.message.chat.id,
                    "\u2705 Sab channels OK. Ab group me search kar sakte ho.",
                )
                await asyncio.sleep(6)
                await tip.delete()
            except Exception:
                pass
        return

    if data == "fs_noop":
        try:
            await query.answer()
        except Exception:
            pass
        return


def register_force_sub_handlers(bot: Client):
    @bot.on_callback_query(filters.regex(r"^(fs_|force_sub_)"))
    async def _fs_cb(client: Client, query: CallbackQuery):
        await handle_fs_callback(client, query)

    @bot.on_message(filters.command(["add_force_chnl", "add_force_channel"]) & (filters.group | filters.private))
    async def add_force_cmd(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in (Config.ADMINS or []):
            return
        channel_id = None
        title = ""
        username = ""
        if message.reply_to_message and message.reply_to_message.forward_from_chat:
            ch = message.reply_to_message.forward_from_chat
            channel_id = ch.id
            title = ch.title or ""
            username = ch.username or ""
        else:
            text = (message.text or "").split(None, 1)
            arg = text[1] if len(text) > 1 else ""
            if arg.startswith("@"):
                try:
                    chat = await client.get_chat(arg)
                    channel_id = chat.id
                    title = chat.title or arg
                    username = chat.username or arg.lstrip("@")
                except Exception as e:
                    await message.reply_text(f"Channel not found: {e}", quote=True)
                    return
            elif arg.lstrip("-").isdigit():
                channel_id = int(arg)
                try:
                    chat = await client.get_chat(channel_id)
                    title = chat.title or str(channel_id)
                    username = chat.username or ""
                except Exception:
                    title = str(channel_id)
            else:
                await message.reply_text(
                    "Usage:\n"
                    "1) Forward a channel post and reply /add_force_chnl\n"
                    "2) /add_force_chnl @channel\n"
                    "3) /add_force_chnl -100xxxxxxxxxx",
                    quote=True,
                )
                return
        if not channel_id:
            await message.reply_text("Could not detect channel.", quote=True)
            return
        try:
            member = await client.get_chat_member(channel_id, "me")
            if not _status_is_admin(member.status):
                await message.reply_text("Bot is not admin in that channel.", quote=True)
                return
        except Exception as e:
            await message.reply_text(f"Cannot check bot admin status: {e}", quote=True)
            return
        _FS_PENDING[int(channel_id)] = {"title": title, "username": username}
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Normal Join", callback_data=f"fs_mode:normal:{int(channel_id)}"),
                InlineKeyboardButton("Request Join", callback_data=f"fs_mode:request:{int(channel_id)}"),
            ],
            [InlineKeyboardButton("Cancel", callback_data=f"fs_mode:cancel:{int(channel_id)}")],
        ])
        await message.reply_text(
            f"Select mode for <b>{_safe_text(title) or channel_id}</b>",
            reply_markup=kb,
            quote=True,
        )

    @bot.on_message(filters.command(["del_force_chnl", "del_force_channel"]) & (filters.group | filters.private))
    async def del_force_cmd(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in (Config.ADMINS or []):
            return
        text = (message.text or "").split(None, 1)
        if len(text) < 2:
            await message.reply_text("Usage: /del_force_chnl -100xxxxxxxxxx", quote=True)
            return
        try:
            cid = int(text[1].strip())
        except Exception:
            await message.reply_text("Invalid channel id", quote=True)
            return
        try:
            ok = await db.remove_force_channel(cid)
            if ok:
                await message.reply_text(f"Removed force-sub channel <code>{cid}</code>", quote=True)
            else:
                await message.reply_text("Channel not in force-sub list.", quote=True)
        except Exception as e:
            await message.reply_text(f"Error: {e}", quote=True)

    @bot.on_message(filters.command(["see_force_sub_chnl", "see_force_channels"]) & (filters.group | filters.private))
    async def list_force_cmd(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in (Config.ADMINS or []):
            return
        try:
            chs = await db.list_force_channels()
        except Exception:
            chs = []
        if not chs:
            await message.reply_text("No force-sub channels.", quote=True)
            return
        lines = []
        for c in chs:
            lines.append(
                f"\u2022 <b>{_safe_text(c.get('title') or '')}</b>\n"
                f"  ID: <code>{c.get('channel_id')}</code> | Mode: <code>{c.get('mode', 'normal')}</code>"
            )
        await message.reply_text("Force-sub channels:\n\n" + "\n\n".join(lines), quote=True)

    @bot.on_message(filters.command(["set_force_sub_message"]) & (filters.group | filters.private))
    async def set_fs_msg(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in (Config.ADMINS or []):
            return
        text = (message.text or "")
        for cmd in ("/set_force_sub_message",):
            if text.lower().startswith(cmd):
                text = text[len(cmd):].strip()
                break
        if not text and message.reply_to_message:
            text = message.reply_to_message.text or message.reply_to_message.caption or ""
        if not text:
            await message.reply_text(
                "Usage: /set_force_sub_message Hello {name}\\nJoin channel...\n"
                "Placeholders: {name} {username} {user}",
                quote=True,
            )
            return
        try:
            await db.set_force_sub_message(text)
            await message.reply_text("\u2705 Custom force-sub message set.", quote=True)
        except Exception as e:
            await message.reply_text(f"Error: {e}", quote=True)

    @bot.on_message(filters.command(["del_force_sub_message"]) & (filters.group | filters.private))
    async def del_fs_msg(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in (Config.ADMINS or []):
            return
        try:
            await db.clear_force_sub_message()
            await message.reply_text("\u2705 Custom message deleted. Default use hoga.", quote=True)
        except Exception as e:
            await message.reply_text(f"Error: {e}", quote=True)

    logger.info("Force-sub handlers registered (owner-lock + 5min auto-delete)")
