# ==============================================================================
# handlers/admin_panel.py — Admin Panel + Force Sub UI (owner/admin only)
# ==============================================================================
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from configs import Config
from handlers.database import db

logger = logging.getLogger("AdminPanel")

_AP_STATE = {}


def _is_admin(uid) -> bool:
    try:
        uid = int(uid)
        admins = Config.ADMINS
        if not admins:
            return False
        return uid in admins
    except Exception:
        return False


def _safe(s) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return ""
    return re.sub(r"[\ud800-\udfff]", "", s)


def kb_admin_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔐 Force Sub", callback_data="ap_fs_menu")],
        [InlineKeyboardButton("❌ Close", callback_data="ap_close")],
    ])


def kb_fs_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Force Sub Channel", callback_data="ap_fs_add")],
        [InlineKeyboardButton("📋 See Force Sub Channels", callback_data="ap_fs_list")],
        [InlineKeyboardButton("💬 Force Sub Messages", callback_data="ap_fs_msg_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_home")],
    ])


def kb_fs_msg_menu(has_msg: bool, has_pic: bool):
    rows = [[InlineKeyboardButton("✏️ Set Force Sub Message", callback_data="ap_fs_set_msg")]]
    if has_msg:
        rows.append([InlineKeyboardButton("👁 See Force Sub Message", callback_data="ap_fs_see_msg")])
    rows.append([InlineKeyboardButton("🖼 Set Force Sub Pic", callback_data="ap_fs_set_pic")])
    if has_pic:
        rows.append([InlineKeyboardButton("👁 See Force Sub Pic", callback_data="ap_fs_see_pic")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_menu")])
    return InlineKeyboardMarkup(rows)


def kb_mode_pick(channel_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Normal Join", callback_data=f"ap_fs_mode:normal:{channel_id}")],
        [InlineKeyboardButton("📩 Request Join", callback_data=f"ap_fs_mode:request:{channel_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_menu")],
    ])


def kb_back_fs():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_menu")]])


def kb_channel_actions(channel_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Channel", callback_data=f"ap_fs_del:{channel_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_list")],
    ])


def kb_msg_actions():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Force Sub Message", callback_data="ap_fs_del_msg")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_msg_menu")],
    ])


def kb_pic_actions():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Force Sub Pic", callback_data="ap_fs_del_pic")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_msg_menu")],
    ])


async def _get_fs_msg() -> str:
    try:
        return (await db.get_force_sub_message()) or ""
    except Exception:
        return ""


async def _get_fs_pic() -> str:
    try:
        return (await db.get_force_sub_pic()) or ""
    except Exception:
        return ""


async def _edit_or_reply(query: CallbackQuery, text: str, markup=None):
    text = _safe(text)
    try:
        await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        return
    except Exception as e1:
        logger.warning("edit_text: %s", e1)
    try:
        await query.message.reply_text(text, reply_markup=markup, disable_web_page_preview=True, quote=True)
    except Exception as e2:
        logger.warning("reply_text: %s", e2)
        try:
            await query.answer(str(e2)[:180], show_alert=True)
        except Exception:
            pass


async def _resolve_channel(client: Client, message: Message):
    if message.forward_from_chat:
        ch = message.forward_from_chat
        return int(ch.id), _safe(getattr(ch, "title", None) or ""), _safe(getattr(ch, "username", None) or "")

    text = (message.text or message.caption or "").strip()
    if not text:
        return None, None, None

    if re.fullmatch(r"-?\d{5,}", text):
        cid = int(text)
        try:
            chat = await client.get_chat(cid)
            return int(chat.id), _safe(chat.title or ""), _safe(chat.username or "")
        except Exception as e:
            logger.warning("get_chat id: %s", e)
            return cid, str(cid), ""

    m = re.search(r"(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{4,})", text)
    if m:
        uname = m.group(1)
        if uname.lower() in ("c", "joinchat", "addstickers"):
            m2 = re.search(r"t\.me/c/(\d+)", text)
            if m2:
                cid = int("-100" + m2.group(1))
                try:
                    chat = await client.get_chat(cid)
                    return int(chat.id), _safe(chat.title or ""), _safe(chat.username or "")
                except Exception as e:
                    logger.warning("get_chat c/: %s", e)
                    return cid, str(cid), ""
            return None, None, None
        try:
            chat = await client.get_chat(uname)
            return int(chat.id), _safe(chat.title or ""), _safe(chat.username or uname)
        except Exception as e:
            logger.warning("get_chat uname: %s", e)
            return None, None, None

    return None, None, None


async def _bot_is_admin(client: Client, channel_id: int) -> bool:
    try:
        me = await client.get_me()
        m = await client.get_chat_member(channel_id, me.id)
        st = str(getattr(m.status, "value", m.status) or m.status).lower()
        if any(x in st for x in ("administrator", "owner", "creator")):
            return True
        if getattr(m, "privileges", None) is not None:
            return True
    except Exception as e:
        logger.warning("bot admin check %s: %s", channel_id, e)
    return False


async def handle_ap_callback(client: Client, query: CallbackQuery):
    """Main entry for all ap_* callbacks (called from session_callbacks + own handler)."""
    user = query.from_user
    data = (query.data or "").strip()
    logger.info("AP cb data=%s user=%s", data, getattr(user, "id", None))

    if not user:
        try:
            await query.answer()
        except Exception:
            pass
        return

    if not _is_admin(user.id):
        try:
            await query.answer("Sirf owner/admin ke liye.", show_alert=True)
        except Exception:
            pass
        return

    uid = int(user.id)
    try:
        await query.answer()
    except Exception:
        pass

    if data == "ap_close":
        _AP_STATE.pop(uid, None)
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_text("Admin panel band.")
            except Exception:
                pass
        return

    if data == "ap_home":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(
            query,
            "🛠 <b>Admin Panel</b>\n\nSirf owner/admin ke liye.\nNeeche se option choose karo:",
            kb_admin_home(),
        )
        return

    if data == "ap_fs_menu":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(
            query,
            "🔐 <b>Force Sub Panel</b>\n\nChannel add/remove, message & pic set karo:",
            kb_fs_menu(),
        )
        return

    if data == "ap_fs_add":
        _AP_STATE[uid] = {"step": "wait_channel"}
        await _edit_or_reply(
            query,
            "➕ <b>Add Force Sub Channel</b>\n\n"
            "Mujhe bhejo:\n"
            "• Channel ID (<code>-1001234567890</code>)\n"
            "• Channel link / @username\n"
            "• Channel se message <b>forward</b>\n\n"
            "Bot us channel me <b>admin</b> hona chahiye.",
            kb_back_fs(),
        )
        return

    if data.startswith("ap_fs_mode:"):
        parts = data.split(":")
        if len(parts) < 3:
            return
        mode = parts[1]
        try:
            cid = int(parts[2])
        except Exception:
            return
        st = _AP_STATE.get(uid) or {}
        title = st.get("title") or str(cid)
        username = st.get("username") or ""
        try:
            await db.add_force_channel(cid, title=title, username=username, mode=mode)
            _AP_STATE.pop(uid, None)
            await _edit_or_reply(
                query,
                f"✅ Force sub channel add ho gaya.\n\n"
                f"<b>{_safe(title)}</b>\nID: <code>{cid}</code>\nMode: <code>{mode}</code>",
                kb_fs_menu(),
            )
        except ValueError as ve:
            msg = "❌ Max 7 force-sub channels allowed." if str(ve) == "MAX_7" else f"❌ {ve}"
            await _edit_or_reply(query, msg, kb_fs_menu())
        except Exception as e:
            logger.exception("add_force_channel")
            await _edit_or_reply(query, f"❌ Error: {type(e).__name__}: {e}", kb_fs_menu())
        return

    if data == "ap_fs_list":
        try:
            chs = await db.list_force_channels()
        except Exception:
            chs = []
        if not chs:
            await _edit_or_reply(query, "Koi force-sub channel nahi hai.", kb_fs_menu())
            return
        rows = []
        for c in chs:
            cid = c.get("channel_id")
            title = _safe(c.get("title") or str(cid))[:40]
            mode = c.get("mode") or "normal"
            rows.append([InlineKeyboardButton(f"{title} [{mode}]", callback_data=f"ap_fs_view:{cid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_menu")])
        await _edit_or_reply(
            query,
            "📋 <b>Force Sub Channels</b>\n\nClick karke delete kar sakte ho:",
            InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("ap_fs_view:"):
        try:
            cid = int(data.split(":")[1])
        except Exception:
            return
        try:
            chs = await db.list_force_channels()
        except Exception:
            chs = []
        ch = next((x for x in chs if int(x.get("channel_id", 0)) == cid), None)
        if not ch:
            await _edit_or_reply(query, "Channel nahi mila.", kb_fs_menu())
            return
        text = (
            f"📢 <b>{_safe(ch.get('title') or '')}</b>\n"
            f"ID: <code>{cid}</code>\n"
            f"Username: @{_safe(ch.get('username') or '-')}\n"
            f"Mode: <code>{ch.get('mode') or 'normal'}</code>"
        )
        await _edit_or_reply(query, text, kb_channel_actions(cid))
        return

    if data.startswith("ap_fs_del:"):
        try:
            cid = int(data.split(":")[1])
        except Exception:
            return
        try:
            await db.remove_force_channel(cid)
            await _edit_or_reply(query, f"✅ Channel <code>{cid}</code> delete.", kb_fs_menu())
        except Exception as e:
            await _edit_or_reply(query, f"❌ Delete fail: {e}", kb_fs_menu())
        return

    if data == "ap_fs_msg_menu":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(
            query,
            "💬 <b>Force Sub Messages</b>\n\nMessage / pic set karo:",
            kb_fs_msg_menu(bool(await _get_fs_msg()), bool(await _get_fs_pic())),
        )
        return

    if data == "ap_fs_set_msg":
        _AP_STATE[uid] = {"step": "wait_msg"}
        await _edit_or_reply(
            query,
            "✏️ <b>Set Force Sub Message</b>\n\nNaya message bhejo.\n"
            "Placeholders: <code>{name}</code> <code>{username}</code> <code>{user}</code>",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_msg_menu")]]),
        )
        return

    if data == "ap_fs_see_msg":
        msg = await _get_fs_msg()
        if not msg:
            await _edit_or_reply(query, "Koi custom message set nahi.", kb_fs_msg_menu(False, bool(await _get_fs_pic())))
            return
        await _edit_or_reply(query, f"👁 <b>Current:</b>\n\n{_safe(msg)}", kb_msg_actions())
        return

    if data == "ap_fs_del_msg":
        try:
            await db.clear_force_sub_message()
        except Exception as e:
            logger.warning("clear msg: %s", e)
        await _edit_or_reply(query, "✅ Message delete.", kb_fs_msg_menu(False, bool(await _get_fs_pic())))
        return

    if data == "ap_fs_set_pic":
        _AP_STATE[uid] = {"step": "wait_pic"}
        await _edit_or_reply(
            query,
            "🖼 <b>Set Force Sub Pic</b>\n\nEk photo bhejo.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_msg_menu")]]),
        )
        return

    if data == "ap_fs_see_pic":
        pic = await _get_fs_pic()
        if not pic:
            await _edit_or_reply(query, "Koi pic set nahi.", kb_fs_msg_menu(bool(await _get_fs_msg()), False))
            return
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await client.send_photo(query.message.chat.id, pic, caption="👁 Current Force Sub Pic", reply_markup=kb_pic_actions())
        except Exception as e:
            await client.send_message(query.message.chat.id, f"Pic fail: {e}", reply_markup=kb_fs_msg_menu(bool(await _get_fs_msg()), True))
        return

    if data == "ap_fs_del_pic":
        try:
            await db.clear_force_sub_pic()
        except Exception as e:
            logger.warning("clear pic: %s", e)
        await _edit_or_reply(query, "✅ Pic delete.", kb_fs_msg_menu(bool(await _get_fs_msg()), False))
        return

    await _edit_or_reply(query, "🛠 <b>Admin Panel</b>\n\nOption choose karo:", kb_admin_home())


def register_admin_panel_handlers(bot: Client):
    @bot.on_callback_query(filters.regex(r"^ap_"), group=-2)
    async def ap_callbacks(client: Client, query: CallbackQuery):
        await handle_ap_callback(client, query)

    @bot.on_message(filters.private, group=50)
    async def ap_admin_input(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            uid = int(message.from_user.id)
            if not _is_admin(uid):
                return
            st = _AP_STATE.get(uid)
            if not st:
                return
            text0 = (message.text or "").strip()
            if text0.startswith("/"):
                return

            step = st.get("step")

            if step == "wait_channel":
                cid, title, username = await _resolve_channel(client, message)
                if not cid:
                    await message.reply_text(
                        "❌ Channel samajh nahi aaya.\nID / @username / link / forward bhejo.",
                        quote=True, reply_markup=kb_back_fs(),
                    )
                    return
                if not await _bot_is_admin(client, cid):
                    await message.reply_text(
                        f"❌ Bot is channel me admin nahi.\nPehle bot ko <b>{_safe(title) or cid}</b> me admin banao.",
                        quote=True, reply_markup=kb_back_fs(),
                    )
                    return
                _AP_STATE[uid] = {
                    "step": "wait_mode",
                    "channel_id": cid,
                    "title": title or str(cid),
                    "username": username or "",
                }
                await message.reply_text(
                    f"✅ Bot admin confirmed.\n\n<b>{_safe(title) or cid}</b>\n"
                    f"ID: <code>{cid}</code>\n\nAb mode choose karo:",
                    quote=True, reply_markup=kb_mode_pick(cid),
                )
                return

            if step == "wait_msg":
                text = (message.text or message.caption or "").strip()
                if not text:
                    await message.reply_text("Text message bhejo.", quote=True)
                    return
                try:
                    await db.set_force_sub_message(text)
                    _AP_STATE.pop(uid, None)
                    await message.reply_text(
                        "✅ Force sub message set.",
                        quote=True,
                        reply_markup=kb_fs_msg_menu(True, bool(await _get_fs_pic())),
                    )
                except Exception as e:
                    await message.reply_text(f"Error: {e}", quote=True)
                return

            if step == "wait_pic":
                if not message.photo:
                    await message.reply_text("Sirf photo bhejo.", quote=True)
                    return
                try:
                    await db.set_force_sub_pic(message.photo.file_id)
                    _AP_STATE.pop(uid, None)
                    await message.reply_text(
                        "✅ Force sub pic set.",
                        quote=True,
                        reply_markup=kb_fs_msg_menu(bool(await _get_fs_msg()), True),
                    )
                except Exception as e:
                    await message.reply_text(f"Error: {e}", quote=True)
                return
        except Exception as e:
            logger.exception("ap_admin_input: %s", e)

    logger.info("Admin panel handlers registered OK")
