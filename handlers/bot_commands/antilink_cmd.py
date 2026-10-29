# ==============================================================================
# handlers/bot_commands/antilink_cmd.py
# Anti-link / anti-username: delete + 10min suspend (x2) then permanent ban.
# /unbanlink — owner/admin unban after link-ban or other chat ban.
# ==============================================================================

import re
import time
import logging
import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, ChatPermissions
from pyrogram.errors import FloodWait, UserAdminInvalid, ChatAdminRequired

from configs import Config

logger = logging.getLogger("AntiLinkCmd")

# user_id -> strike count (1, 2, 3+)
_STRIKES: dict = {}
# user_id -> last action time (anti double-fire)
_LAST: dict = {}

# links + t.me + domains + @username
_LINK_RE = re.compile(
    r"(?i)"
    r"("
    r"https?://\S+"
    r"|www\.\S+"
    r"|t\.me/\S+"
    r"|telegram\.me/\S+"
    r"|telegram\.dog/\S+"
    r"|(?:[\w-]+\.)+(?:com|net|org|in|io|me|co|xyz|info|tk|ml|ga|cf|app|dev|link|site|online|store|shop|pro|tv|cc)\b\S*"
    r")"
)
_USER_RE = re.compile(r"(?<!\w)@([A-Za-z][A-Za-z0-9_]{3,31})\b")


def _is_config_admin(user_id: int) -> bool:
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
    return False


async def _is_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    if _is_config_admin(user_id):
        return True
    try:
        m = await client.get_chat_member(chat_id, user_id)
        st = m.status
        if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
        s = str(getattr(st, "value", st) or st).lower()
        if any(x in s for x in ("administrator", "owner", "creator")):
            return True
    except Exception:
        pass
    return False


def _has_forbidden(text: str):
    if not text:
        return False, ""
    m = _LINK_RE.search(text)
    if m:
        return True, f"link: {m.group(0)[:80]}"
    m = _USER_RE.search(text)
    if m:
        return True, f"username: @{m.group(1)}"
    return False, ""


def _mute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_other_messages=False,
        can_send_polls=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
    )


def _full_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_other_messages=True,
        can_send_polls=True,
        can_add_web_page_previews=True,
        can_invite_users=True,
    )


async def _notify_owner(client: Client, chat_id: int, text: str):
    log_ch = getattr(Config, "LOG_CHANNEL", None)
    if log_ch:
        try:
            await client.send_message(int(log_ch), text)
            return
        except Exception as e:
            logger.warning("log notify: %s", e)
    owner = getattr(Config, "OWNER_ID", None)
    if owner:
        try:
            await client.send_message(int(owner), text)
        except Exception as e:
            logger.warning("owner notify: %s", e)


async def _punish(client: Client, message: Message, reason: str):
    chat_id = message.chat.id
    user = message.from_user
    if not user:
        return
    uid = user.id
    name = (user.first_name or "User").replace("<", "").replace(">", "")
    mention = f'<a href="tg://user?id={uid}">{name}</a>'

    try:
        await message.delete()
    except Exception as e:
        logger.warning("delete msg: %s", e)

    strikes = _STRIKES.get(uid, 0) + 1
    _STRIKES[uid] = strikes

    action = ""
    try:
        if strikes >= 3:
            try:
                await client.ban_chat_member(chat_id, uid)
                action = "PERMANENT BAN"
            except Exception as e:
                logger.warning("ban: %s", e)
                action = f"BAN fail: {type(e).__name__}"
            warn_text = (
                f"\U0001f6ab {mention} (<code>{uid}</code>)\n"
                f"Strike: <b>{strikes}/3</b>\n"
                f"Reason: <code>{reason}</code>\n"
                f"Action: <b>PERMANENT BAN</b>\n\n"
                f"Unban: <code>/unbanlink {uid}</code>"
            )
        else:
            until = datetime.now(timezone.utc) + timedelta(minutes=10)
            try:
                await client.restrict_chat_member(
                    chat_id,
                    uid,
                    permissions=_mute_permissions(),
                    until_date=until,
                )
                action = "10 min SUSPEND"
            except Exception as e:
                logger.warning("restrict: %s", e)
                action = f"SUSPEND fail: {type(e).__name__}"
            warn_text = (
                f"\u26a0\ufe0f {mention} (<code>{uid}</code>)\n"
                f"Strike: <b>{strikes}/3</b>\n"
                f"Reason: <code>{reason}</code>\n"
                f"Action: <b>10 minute suspend</b>\n\n"
                f"3rd time = permanent ban.\n"
                f"Unban: <code>/unbanlink {uid}</code>"
            )
    except Exception as e:
        logger.exception("punish: %s", e)
        warn_text = f"\u274c Punish error for {uid}: {e}"
        action = "error"

    try:
        notice = await client.send_message(chat_id, warn_text)

        async def _del_later(mid):
            await asyncio.sleep(60)
            try:
                await client.delete_messages(chat_id, mid)
            except Exception:
                pass

        asyncio.create_task(_del_later(notice.id))
    except Exception as e:
        logger.warning("group notice: %s", e)

    await _notify_owner(
        client,
        chat_id,
        f"\U0001f6e1 AntiLink\nChat: <code>{chat_id}</code>\n"
        f"User: {name} (<code>{uid}</code>)\n"
        f"Strike: {strikes}/3\nReason: {reason}\nAction: {action}",
    )


def register_antilink_command(bot: Client):
    @bot.on_message(
        filters.group & ~filters.service & (filters.text | filters.caption),
        group=5,
    )
    async def antilink_watch(client: Client, message: Message):
        try:
            if not message.from_user or message.from_user.is_bot:
                return
            uid = message.from_user.id
            chat_id = message.chat.id

            if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
                return

            if await _is_group_admin(client, chat_id, uid):
                return

            text = message.text or message.caption or ""
            bad, reason = _has_forbidden(text)
            if not bad and message.entities:
                for ent in message.entities:
                    et_l = str(getattr(ent, "type", "") or "").lower()
                    if "url" in et_l or "text_link" in et_l:
                        url = getattr(ent, "url", None) or text[ent.offset: ent.offset + ent.length]
                        bad, reason = True, f"link: {str(url)[:80]}"
                        break
                    if "mention" in et_l:
                        uname = text[ent.offset: ent.offset + ent.length]
                        bad, reason = True, f"username: {uname}"
                        break
            if not bad and message.caption_entities:
                for ent in message.caption_entities:
                    et_l = str(getattr(ent, "type", "") or "").lower()
                    if "url" in et_l or "text_link" in et_l or "mention" in et_l:
                        bad, reason = True, "caption link/username"
                        break
            if not bad:
                return

            now = time.time()
            if now - _LAST.get(uid, 0) < 2:
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            _LAST[uid] = now

            await _punish(client, message, reason)
        except Exception as e:
            logger.exception("antilink_watch: %s", e)

    @bot.on_message(
        filters.command(["unbanlink", "ulink", "unban_link"]) & filters.group,
        group=-3,
    )
    async def unbanlink_cmd(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            chat_id = message.chat.id
            if not await _is_group_admin(client, chat_id, message.from_user.id):
                await message.reply_text("\U0001f6ab Sirf admin/owner use kar sakte hain.")
                return

            target_id = None
            if message.reply_to_message and message.reply_to_message.from_user:
                target_id = message.reply_to_message.from_user.id
            else:
                parts = (message.text or "").split()
                if len(parts) >= 2:
                    try:
                        target_id = int(parts[1].strip())
                    except Exception:
                        u = parts[1].strip().lstrip("@")
                        try:
                            chat = await client.get_users(u)
                            target_id = chat.id
                        except Exception:
                            pass

            if not target_id:
                await message.reply_text(
                    "Usage:\n"
                    "\u2022 User pe reply karke <code>/unbanlink</code>\n"
                    "\u2022 <code>/unbanlink USER_ID</code>\n"
                    "\u2022 <code>/unbanlink @username</code>"
                )
                return

            try:
                await client.unban_chat_member(chat_id, target_id)
            except Exception as e:
                logger.warning("unban_chat_member: %s", e)
            try:
                await client.restrict_chat_member(
                    chat_id,
                    target_id,
                    permissions=_full_permissions(),
                )
            except Exception as e:
                logger.warning("restore perms: %s", e)

            _STRIKES.pop(target_id, None)
            _LAST.pop(target_id, None)

            await message.reply_text(
                f"\u2705 User <code>{target_id}</code> unban/unmute ho gaya.\n"
                f"Strikes reset."
            )
            await _notify_owner(
                client,
                chat_id,
                f"\U0001f6e1 AntiLink UNBAN\nChat: <code>{chat_id}</code>\n"
                f"User: <code>{target_id}</code>\n"
                f"By: <code>{message.from_user.id}</code>",
            )
        except Exception as e:
            logger.exception("unbanlink: %s", e)
            try:
                await message.reply_text(f"\u274c Error: {type(e).__name__}: {e}")
            except Exception:
                pass

    logger.info("antilink watch + /unbanlink registered")
