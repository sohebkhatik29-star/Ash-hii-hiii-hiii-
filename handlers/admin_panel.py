# ==============================================================================
# handlers/admin_panel.py — Admin Panel + Force Sub + Caption + Auto Delete + Manage Admin
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

try:
    from handlers import caption_db  # noqa: F401
except Exception:
    pass

from handlers.auto_delete import (
    parse_duration,
    format_duration,
    get_bot_file_delete_seconds,
    set_bot_file_delete_seconds,
    get_bot_delete_notice,
    set_bot_delete_notice,
    clear_bot_delete_notice,
    get_group_result_delete_seconds,
    set_group_result_delete_seconds,
    get_group_not_found_delete_seconds,
    set_group_not_found_delete_seconds,
)
from handlers.admins_db import (
    list_all_admins,
    add_bot_admin,
    remove_bot_admin,
    promote_in_group,
    demote_in_group,
    sync_admins_to_config,
)

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
        [InlineKeyboardButton("📝 File Caption", callback_data="ap_cap_menu")],
        [InlineKeyboardButton("⏱ Set Auto Delete", callback_data="ap_ad_menu")],
        [InlineKeyboardButton("👑 Manage Admin", callback_data="ap_ma_menu")],
        [InlineKeyboardButton("❌ Close", callback_data="ap_close")],
    ])


def kb_ma_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Admin", callback_data="ap_ma_add")],
        [InlineKeyboardButton("📋 See Admins", callback_data="ap_ma_list")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_home")],
    ])


def kb_admin_actions(user_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Admin", callback_data=f"ap_ma_del:{user_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_ma_list")],
    ])


def kb_ad_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 See Auto Delete Time", callback_data="ap_ad_see_bot")],
        [InlineKeyboardButton("➕ Set Auto Delete Time (Bot File)", callback_data="ap_ad_set_bot")],
        [InlineKeyboardButton("💬 Set Auto Delete Message", callback_data="ap_ad_set_notice")],
        [InlineKeyboardButton("👥 Group Delete Management", callback_data="ap_ad_grp_menu")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_home")],
    ])


def kb_ad_grp_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Result User+Bot Delete Timing", callback_data="ap_ad_set_result")],
        [InlineKeyboardButton("❌ Not-Result User Delete Timing", callback_data="ap_ad_set_notfound")],
        [InlineKeyboardButton("👁 See Group Timings", callback_data="ap_ad_see_grp")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_ad_menu")],
    ])


def kb_cap_menu(has_cap: bool):
    rows = [[InlineKeyboardButton("✏️ Set Caption", callback_data="ap_cap_set")]]
    if has_cap:
        rows.append([InlineKeyboardButton("👁 See Caption", callback_data="ap_cap_see")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_home")])
    return InlineKeyboardMarkup(rows)


def kb_cap_see_actions():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Caption", callback_data="ap_cap_del")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_cap_menu")],
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


async def _get_file_cap() -> str:
    try:
        return (await db.get_file_caption()) or ""
    except Exception:
        return ""


async def _edit_or_reply(query: CallbackQuery, text: str, markup=None):
    text = _safe(text)
    try:
        await query.message.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        return
    except Exception as e1:
        err = str(e1).lower()
        if "not modified" in err or "message is not modified" in err:
            return
        logger.warning("edit_text: %s", e1)
    try:
        await query.answer("Updated", show_alert=False)
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


async def _resolve_user(client: Client, message: Message):
    """Resolve user_id, name, username from id / @username / forward / text."""
    if message.forward_from:
        u = message.forward_from
        return int(u.id), _safe(u.first_name or ""), _safe(u.username or "")

    text = (message.text or message.caption or "").strip()
    if not text:
        return None, None, None

    if re.fullmatch(r"\d{5,}", text):
        uid = int(text)
        try:
            chat = await client.get_users(uid)
            return int(chat.id), _safe(chat.first_name or ""), _safe(chat.username or "")
        except Exception as e:
            logger.warning("get_users id: %s", e)
            return uid, str(uid), ""

    m = re.search(r"(?:t\.me/|telegram\.me/|@)?([A-Za-z0-9_]{4,})", text)
    if m:
        uname = m.group(1)
        if uname.lower() in ("c", "joinchat", "addstickers"):
            return None, None, None
        try:
            chat = await client.get_users(uname)
            return int(chat.id), _safe(chat.first_name or ""), _safe(chat.username or uname)
        except Exception as e:
            logger.warning("get_users uname: %s", e)
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


_TIME_HELP = (
    "Time examples:\n"
    "• <code>1m</code> <code>2m</code> <code>30m</code>\n"
    "• <code>1h</code> <code>2h</code>\n"
    "• <code>1d</code> <code>2d</code> <code>24h</code>\n"
    "• <code>3month</code> <code>4mahine</code>\n"
    "• ya sirf seconds: <code>1800</code>"
)


async def handle_ap_callback(client: Client, query: CallbackQuery):
    user = query.from_user
    data = (query.data or "").strip()
    logger.info("AP cb data=%s user=%s", data, getattr(user, "id", None))

    if not user:
        try:
            await query.answer()
        except Exception:
            pass
        return

    try:
        await sync_admins_to_config()
    except Exception:
        pass

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

    # ---------- Manage Admin ----------
    if data == "ap_ma_menu":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(
            query,
            "👑 <b>Manage Admin</b>\n\n"
            "Bot admin add/remove. Add pe group me bhi auto-promote hoga.",
            kb_ma_menu(),
        )
        return

    if data == "ap_ma_add":
        _AP_STATE[uid] = {"step": "wait_add_admin"}
        await _edit_or_reply(
            query,
            "➕ <b>Add Admin</b>\n\n"
            "User ko admin banane ke liye bhejo:\n"
            "• User ID (<code>123456789</code>)\n"
            "• @username\n"
            "• User ka message <b>forward</b>\n\n"
            "Bot + Movie Group dono me admin banega.",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ma_menu")]]),
        )
        return

    if data == "ap_ma_list":
        try:
            admins = await list_all_admins()
        except Exception as e:
            await _edit_or_reply(query, f"Error: {e}", kb_ma_menu())
            return
        if not admins:
            await _edit_or_reply(query, "Koi admin nahi mila.", kb_ma_menu())
            return
        rows = []
        for a in admins:
            aid = a.get("user_id")
            name = _safe(a.get("name") or str(aid))[:28]
            un = a.get("username") or ""
            src = a.get("source") or ""
            label = f"{name}"
            if un:
                label += f" (@{un})"
            if src == "owner":
                label = "👑 " + label
            rows.append([InlineKeyboardButton(label[:60], callback_data=f"ap_ma_view:{aid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_ma_menu")])
        await _edit_or_reply(
            query,
            "📋 <b>Admins</b>\n\nNaam pe click → Delete / Back:",
            InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("ap_ma_view:"):
        try:
            aid = int(data.split(":")[1])
        except Exception:
            return
        try:
            admins = await list_all_admins()
        except Exception:
            admins = []
        a = next((x for x in admins if int(x.get("user_id", 0)) == aid), None)
        if not a:
            await _edit_or_reply(query, "Admin nahi mila.", kb_ma_menu())
            return
        name = _safe(a.get("name") or "")
        un = _safe(a.get("username") or "-")
        src = a.get("source") or ""
        text = (
            f"👤 <b>{name}</b>\n"
            f"ID: <code>{aid}</code>\n"
            f"Username: @{un}\n"
            f"Source: <code>{src}</code>"
        )
        if src == "owner":
            await _edit_or_reply(
                query,
                text + "\n\n⚠️ Owner delete nahi hota.",
                InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ma_list")]]),
            )
        else:
            await _edit_or_reply(query, text, kb_admin_actions(aid))
        return

    if data.startswith("ap_ma_del:"):
        try:
            aid = int(data.split(":")[1])
        except Exception:
            return
        res = await remove_bot_admin(aid)
        grp = await demote_in_group(client, aid)
        if res.get("ok"):
            await _edit_or_reply(
                query,
                f"✅ Admin remove: <code>{aid}</code>\n{grp}",
                kb_ma_menu(),
            )
        else:
            await _edit_or_reply(query, f"❌ {res.get('msg')}", kb_ma_menu())
        return

    # ---------- Auto Delete ----------
    if data == "ap_ad_menu":
        _AP_STATE.pop(uid, None)
        try:
            bot_sec = await get_bot_file_delete_seconds()
            res_sec = await get_group_result_delete_seconds()
            nf_sec = await get_group_not_found_delete_seconds()
        except Exception:
            bot_sec, res_sec, nf_sec = 1800, 86400, 172800
        await _edit_or_reply(
            query,
            "⏱ <b>Set Auto Delete</b>\n\n"
            f"🤖 Bot file: <code>{format_duration(bot_sec)}</code>\n"
            f"✅ Group result: <code>{format_duration(res_sec)}</code>\n"
            f"❌ Group not-found: <code>{format_duration(nf_sec)}</code>",
            kb_ad_menu(),
        )
        return

    if data == "ap_ad_see_bot":
        try:
            sec = await get_bot_file_delete_seconds()
            notice = await get_bot_delete_notice()
        except Exception as e:
            await _edit_or_reply(query, f"Error: {e}", kb_ad_menu())
            return
        await _edit_or_reply(
            query,
            f"👁 <b>Bot File Auto Delete</b>\n\n"
            f"Time: <code>{format_duration(sec)}</code> ({sec}s)\n\n"
            f"Notice message:\n{_safe(notice)}",
            kb_ad_menu(),
        )
        return

    if data == "ap_ad_set_bot":
        _AP_STATE[uid] = {"step": "wait_ad_bot"}
        await _edit_or_reply(
            query,
            "➕ <b>Set Bot File Auto Delete Time</b>\n\n"
            "User jab bot me movie file lega, us file kitne time baad delete ho.\n\n"
            + _TIME_HELP,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ad_menu")]]),
        )
        return

    if data == "ap_ad_set_notice":
        _AP_STATE[uid] = {"step": "wait_ad_notice"}
        await _edit_or_reply(
            query,
            "💬 <b>Set Auto Delete Message</b>\n\n"
            "File ke neeche jo notice aata hai, woh template bhejo.\n"
            "Placeholder: <code>{time}</code>\n\n"
            "Default reset: <code>off</code>",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ad_menu")]]),
        )
        return

    if data == "ap_ad_grp_menu":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(
            query,
            "👥 <b>Group Delete Management</b>\n\n"
            "Result milne / na milne par group me user + bot message kab delete hon:",
            kb_ad_grp_menu(),
        )
        return

    if data == "ap_ad_see_grp":
        try:
            res_sec = await get_group_result_delete_seconds()
            nf_sec = await get_group_not_found_delete_seconds()
        except Exception as e:
            await _edit_or_reply(query, f"Error: {e}", kb_ad_grp_menu())
            return
        await _edit_or_reply(
            query,
            "👁 <b>Group Delete Timings</b>\n\n"
            f"✅ Result mila: <code>{format_duration(res_sec)}</code>\n"
            f"❌ Result nahi mila: <code>{format_duration(nf_sec)}</code>",
            kb_ad_grp_menu(),
        )
        return

    if data == "ap_ad_set_result":
        _AP_STATE[uid] = {"step": "wait_ad_result"}
        await _edit_or_reply(
            query,
            "✅ <b>Result User + Bot Delete Timing</b>\n\n"
            "Group me result milne par user + bot result kitne time baad delete.\n\n"
            + _TIME_HELP,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ad_grp_menu")]]),
        )
        return

    if data == "ap_ad_set_notfound":
        _AP_STATE[uid] = {"step": "wait_ad_notfound"}
        await _edit_or_reply(
            query,
            "❌ <b>Not-Result User Delete Timing</b>\n\n"
            "Group me result na milne par user message kitne time baad delete.\n\n"
            + _TIME_HELP,
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_ad_grp_menu")]]),
        )
        return

    # ---------- File Caption ----------
    if data == "ap_cap_menu":
        _AP_STATE.pop(uid, None)
        has = bool(await _get_file_cap())
        await _edit_or_reply(
            query,
            "📝 <b>File Caption</b>\n\n"
            "Placeholders: <code>{file_name}</code> <code>{file_size}</code> <code>{file_caption}</code>",
            kb_cap_menu(has),
        )
        return

    if data == "ap_cap_set":
        _AP_STATE[uid] = {"step": "wait_caption"}
        await _edit_or_reply(
            query,
            "✏️ <b>Set Caption</b>\n\nCaption template bhejo (HTML OK).\n"
            "• <code>{file_name}</code> • <code>{file_size}</code> • <code>{file_caption}</code>",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_cap_menu")]]),
        )
        return

    if data == "ap_cap_see":
        cap = await _get_file_cap()
        if not cap:
            await _edit_or_reply(query, "Koi custom caption set nahi.", kb_cap_menu(False))
            return
        await _edit_or_reply(query, f"👁 <b>Current:</b>\n\n{_safe(cap)}", kb_cap_see_actions())
        return

    if data == "ap_cap_del":
        try:
            await db.clear_file_caption()
        except Exception as e:
            logger.warning("clear caption: %s", e)
        await _edit_or_reply(query, "✅ Caption delete. Original use hoga.", kb_cap_menu(False))
        return

    # ---------- Force Sub ----------
    if data == "ap_fs_menu":
        _AP_STATE.pop(uid, None)
        await _edit_or_reply(query, "🔐 <b>Force Sub Panel</b>", kb_fs_menu())
        return

    if data == "ap_fs_add":
        _AP_STATE[uid] = {"step": "wait_channel"}
        await _edit_or_reply(
            query,
            "➕ <b>Add Force Sub Channel</b>\n\n"
            "Channel ID / @username / link / forward bhejo.\nBot us channel me admin hona chahiye.",
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
                f"✅ Force sub add.\n<b>{_safe(title)}</b>\nID: <code>{cid}</code>\nMode: <code>{mode}</code>",
                kb_fs_menu(),
            )
        except ValueError as ve:
            msg = "❌ Max 7 force-sub channels." if str(ve) == "MAX_7" else f"❌ {ve}"
            await _edit_or_reply(query, msg, kb_fs_menu())
        except Exception as e:
            await _edit_or_reply(query, f"❌ Error: {e}", kb_fs_menu())
        return

    if data == "ap_fs_list":
        try:
            chs = await db.list_force_channels()
        except Exception:
            chs = []
        if not chs:
            await _edit_or_reply(query, "Koi force-sub channel nahi.", kb_fs_menu())
            return
        rows = []
        for c in chs:
            cid = c.get("channel_id")
            title = _safe(c.get("title") or str(cid))[:40]
            mode = c.get("mode") or "normal"
            rows.append([InlineKeyboardButton(f"{title} [{mode}]", callback_data=f"ap_fs_view:{cid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_fs_menu")])
        await _edit_or_reply(query, "📋 <b>Force Sub Channels</b>", InlineKeyboardMarkup(rows))
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
            "💬 <b>Force Sub Messages</b>",
            kb_fs_msg_menu(bool(await _get_fs_msg()), bool(await _get_fs_pic())),
        )
        return

    if data == "ap_fs_set_msg":
        _AP_STATE[uid] = {"step": "wait_msg"}
        await _edit_or_reply(
            query,
            "✏️ <b>Set Force Sub Message</b>\n\nMessage bhejo.\n"
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
            "🖼 <b>Set Force Sub Pic</b>\n\nPhoto bhejo.",
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

    await _edit_or_reply(query, "🛠 <b>Admin Panel</b>", kb_admin_home())


def register_admin_panel_handlers(bot: Client):

    @bot.on_message(filters.private, group=50)
    async def ap_admin_input(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            uid = int(message.from_user.id)
            try:
                await sync_admins_to_config()
            except Exception:
                pass
            if not _is_admin(uid):
                return
            st = _AP_STATE.get(uid)
            if not st:
                return
            text0 = (message.text or "").strip()
            if text0.startswith("/"):
                return

            step = st.get("step")

            if step == "wait_add_admin":
                aid, name, username = await _resolve_user(client, message)
                if not aid:
                    await message.reply_text(
                        "❌ User samajh nahi aaya.\nUser ID / @username / forward bhejo.",
                        quote=True,
                        reply_markup=kb_ma_menu(),
                    )
                    return
                res = await add_bot_admin(aid, name=name or str(aid), username=username or "")
                grp = await promote_in_group(client, aid)
                _AP_STATE.pop(uid, None)
                await message.reply_text(
                    f"✅ <b>{_safe(name) or aid}</b> bot admin.\n"
                    f"ID: <code>{aid}</code>\n"
                    f"{res.get('msg')}\n"
                    f"Group: {grp}",
                    quote=True,
                    reply_markup=kb_ma_menu(),
                )
                return

            if step in ("wait_ad_bot", "wait_ad_result", "wait_ad_notfound"):
                text = (message.text or "").strip()
                sec = parse_duration(text)
                if sec is None:
                    await message.reply_text(
                        "❌ Time samajh nahi aaya.\nExamples: <code>30m</code> <code>1h</code> <code>24h</code> <code>2d</code>",
                        quote=True,
                    )
                    return
                try:
                    if step == "wait_ad_bot":
                        await set_bot_file_delete_seconds(sec)
                        label = "Bot file"
                        back = kb_ad_menu()
                    elif step == "wait_ad_result":
                        await set_group_result_delete_seconds(sec)
                        label = "Group result"
                        back = kb_ad_grp_menu()
                    else:
                        await set_group_not_found_delete_seconds(sec)
                        label = "Group not-found"
                        back = kb_ad_grp_menu()
                    _AP_STATE.pop(uid, None)
                    await message.reply_text(
                        f"✅ {label}: <code>{format_duration(sec)}</code> ({sec}s)",
                        quote=True,
                        reply_markup=back,
                    )
                except Exception as e:
                    await message.reply_text(f"Error: {e}", quote=True)
                return

            if step == "wait_ad_notice":
                text = (message.text or message.caption or "").strip()
                if not text:
                    await message.reply_text("Message text bhejo (ya off).", quote=True)
                    return
                try:
                    if text.lower() in ("off", "reset", "default", "clear"):
                        await clear_bot_delete_notice()
                        msg = "✅ Notice default pe reset."
                    else:
                        await set_bot_delete_notice(text)
                        msg = "✅ Auto delete notice set."
                    _AP_STATE.pop(uid, None)
                    await message.reply_text(msg, quote=True, reply_markup=kb_ad_menu())
                except Exception as e:
                    await message.reply_text(f"Error: {e}", quote=True)
                return

            if step == "wait_caption":
                text = (message.text or message.caption or "").strip()
                if not text:
                    await message.reply_text("Caption text bhejo.", quote=True)
                    return
                try:
                    await db.set_file_caption(text)
                    _AP_STATE.pop(uid, None)
                    await message.reply_text("✅ File caption set.", quote=True, reply_markup=kb_cap_menu(True))
                except Exception as e:
                    await message.reply_text(f"Error: {e}", quote=True)
                return

            if step == "wait_channel":
                cid, title, username = await _resolve_channel(client, message)
                if not cid:
                    await message.reply_text("❌ Channel samajh nahi aaya.", quote=True, reply_markup=kb_back_fs())
                    return
                if not await _bot_is_admin(client, cid):
                    await message.reply_text(
                        f"❌ Bot is channel me admin nahi: <b>{_safe(title) or cid}</b>",
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
                    f"✅ Bot admin confirmed.\n<b>{_safe(title) or cid}</b>\nID: <code>{cid}</code>\nMode choose karo:",
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
