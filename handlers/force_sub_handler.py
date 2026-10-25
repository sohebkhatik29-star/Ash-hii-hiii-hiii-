"""Force-sub: channel join required before group search. UI = channel name + Joined/Try Again only."""
import logging
from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery, ChatJoinRequest,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.enums import ChatMemberStatus
from configs import Config

logger = logging.getLogger("ForceSub")

try:
    from handlers.database import db
except Exception:
    db = None

# In-memory pending join requests: (user_id, channel_id) -> True
_PENDING_REQUESTS = {}

DEFAULT_FORCE_MSG = (
    "Hello {name}\n\n"
    "Aapne mera channel join nahi kiya hai.\n"
    "Pehle join karo, fir aap group me search / use kar sakte ho."
)

def _safe_text(s):
    if s is None:
        return ""
    try:
        return str(s).encode("utf-8", "ignore").decode("utf-8")
    except Exception:
        return ""

def _display_user(user) -> str:
    if not user:
        return "User"
    un = getattr(user, "username", None)
    if un:
        return f"@{_safe_text(un).lstrip('@')}"
    fn = _safe_text(getattr(user, "first_name", None) or "")
    return fn or "User"

def _status_is_admin(status) -> bool:
    try:
        return status in (
            ChatMemberStatus.OWNER,
            ChatMemberStatus.ADMINISTRATOR,
            getattr(ChatMemberStatus, "CREATOR", None),
        )
    except Exception:
        return False

async def get_invite_link(bot, channel_id: int, request: bool = False):
    try:
        return await bot.create_chat_invite_link(
            channel_id, creates_join_request=request, name="force-sub"
        )
    except Exception as e:
        logger.warning("invite link %s: %s", channel_id, e)
        return None

async def has_pending_join_request(bot, user_id: int, channel_id: int) -> bool:
    key = (user_id, int(channel_id))
    if _PENDING_REQUESTS.get(key):
        return True
    try:
        async for req in bot.get_chat_join_requests(channel_id, limit=50):
            if req.from_user and req.from_user.id == user_id:
                _PENDING_REQUESTS[key] = True
                return True
    except Exception as e:
        logger.debug("pending check %s: %s", channel_id, e)
    return False

async def check_user_in_channel(bot, user_id: int, channel_id: int) -> bool:
    try:
        mem = await bot.get_chat_member(channel_id, user_id)
        st = mem.status
        if st in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            getattr(ChatMemberStatus, "RESTRICTED", None),
        ):
            return True
    except Exception:
        pass
    return False

async def get_missing_channels(bot, user_id: int):
    if not db:
        return []
    try:
        channels = await db.list_force_channels()
    except Exception as e:
        logger.warning("list_force_channels: %s", e)
        return []
    missing = []
    for ch in channels or []:
        cid = int(ch["channel_id"])
        mode = (ch.get("mode") or "normal").lower()
        if await check_user_in_channel(bot, user_id, cid):
            continue
        if mode == "request" and await has_pending_join_request(bot, user_id, cid):
            continue  # pending = ok, skip
        missing.append(ch)
    return missing

async def build_force_sub_prompt(bot, user_name, missing, pending_query=""):
    user_name = _safe_text(user_name) or "User"
    try:
        custom = await db.get_force_sub_message() if db else ""
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
        mode = (ch.get("mode") or "normal").lower()
        invite = await get_invite_link(bot, cid, request=(mode == "request"))
        if invite and getattr(invite, "invite_link", None):
            url = invite.invite_link
        elif ch.get("username"):
            url = f"https://t.me/{str(ch['username']).lstrip('@')}"
        else:
            url = None
        # ONLY channel name (no Request: prefix, no Maine request bhej diya)
        label = (title or str(cid))[:60]
        if url:
            buttons.append([InlineKeyboardButton(label, url=url)])
        else:
            buttons.append([InlineKeyboardButton(f"! {label}", callback_data="fs_noop")])
    cb = f"fs_refresh:{_safe_text(pending_query)[:40]}" if pending_query else "fs_refresh"
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
    if not db:
        return False
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
    try:
        text, markup = await build_force_sub_prompt(
            bot, name, missing, pending_query=query or (message.text or "")
        )
    except Exception as e:
        logger.warning("build force prompt: %s", e)
        text = DEFAULT_FORCE_MSG.format(name=name)
        markup = None
    try:
        await bot.send_message(
            message.chat.id, text, reply_markup=markup, disable_web_page_preview=True
        )
    except Exception as e:
        logger.warning("send force prompt: %s", e)
    return True

def register_force_sub_handlers(bot: Client):
    @bot.on_callback_query(filters.regex(r"^fs_refresh"))
    async def fs_refresh_cb(client: Client, query: CallbackQuery):
        user = query.from_user
        if not user:
            return
        data = query.data or "fs_refresh"
        missing = await get_missing_channels(client, user.id)
        if missing:
            try:
                await query.answer(
                    "Abhi join/request complete nahi.\n"
                    "Channel open karo → Request/Join dabao → "
                    "phir 'Joined / Try Again' dabao.",
                    show_alert=True,
                )
            except Exception:
                pass
            text, markup = await build_force_sub_prompt(
                client, _display_user(user), missing
            )
            try:
                await query.message.edit_text(
                    text, reply_markup=markup, disable_web_page_preview=True
                )
            except Exception:
                pass
            return
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.answer("Verified!")
        except Exception:
            pass

    @bot.on_callback_query(filters.regex(r"^fs_noop"))
    async def fs_noop_cb(client: Client, query: CallbackQuery):
        try:
            await query.answer("Invite link unavailable", show_alert=True)
        except Exception:
            pass

    @bot.on_chat_join_request()
    async def on_chat_join_request(client: Client, request: ChatJoinRequest):
        try:
            uid = request.from_user.id if request.from_user else None
            cid = request.chat.id if request.chat else None
            if uid and cid:
                _PENDING_REQUESTS[(uid, int(cid))] = True
                logger.info("pending join marked user=%s ch=%s", uid, cid)
        except Exception as e:
            logger.debug("join request mark: %s", e)

    logger.info("Force-sub handlers registered")
