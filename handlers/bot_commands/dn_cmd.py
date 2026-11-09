# ==============================================================================
# handlers/bot_commands/dn_cmd.py
# 1) notify_admin_movie_request() — group me movie nahi mili to admin ko request
# 2) /dn — admin bot PM me us request pe reply karke /dn de →
#          group me original user message pe bot reply kare
# ==============================================================================

import logging
import time
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DnCmd")

# admin_notify_message_id -> request meta (TTL ~48h)
_PENDING: dict = {}
_PENDING_TTL = 48 * 3600

DN_GROUP_TEXT = (
    "✅ <b>Your requested movie is now available.</b>\n"
    "Please search again / check the group."
)


def _purge_old():
    now = time.time()
    dead = [k for k, v in _PENDING.items() if now - v.get("ts", 0) > _PENDING_TTL]
    for k in dead:
        _PENDING.pop(k, None)


async def _is_admin_user(client: Client, user_id: int, chat_id: int = None) -> bool:
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
    if getattr(Config, "BOT_OWNER", None) and user_id == Config.BOT_OWNER:
        return True
    if chat_id:
        try:
            member = await client.get_chat_member(chat_id, user_id)
            st = member.status
            if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
            st_s = str(getattr(st, "value", st) or st).lower()
            if any(x in st_s for x in ("administrator", "owner", "creator")):
                return True
        except Exception:
            pass
    return False


def _admin_targets() -> list:
    """Who receives not-found requests."""
    ids = set()
    try:
        if getattr(Config, "BOT_OWNER", None):
            ids.add(int(Config.BOT_OWNER))
    except Exception:
        pass
    try:
        for a in (Config.ADMINS or set()):
            ids.add(int(a))
    except Exception:
        pass
    return list(ids) or []


async def notify_admin_movie_request(client: Client, message: Message, query: str) -> None:
    """
    Call when group search returns 0 results.
    Forwards user message (tag) + sends details to owner/admins.
    Stores pending so /dn on that notify can reply in group.
    """
    if not message or not message.from_user or not message.chat:
        return
    user = message.from_user
    chat = message.chat
    query = (query or "").strip()
    if not query:
        return

    targets = _admin_targets()
    if not targets:
        logger.warning("dn: no admin targets configured")
        return

    uname = f"@{user.username}" if user.username else "—"
    name = user.first_name or ""
    if user.last_name:
        name = f"{name} {user.last_name}".strip()
    chat_title = getattr(chat, "title", None) or str(chat.id)

    # deep link to original message if public username exists
    msg_link = ""
    try:
        if getattr(chat, "username", None):
            msg_link = f"https://t.me/{chat.username}/{message.id}"
        else:
            # private supergroup: t.me/c/<id without -100>/msg
            cid = str(chat.id)
            if cid.startswith("-100"):
                msg_link = f"https://t.me/c/{cid[4:]}/{message.id}"
    except Exception:
        msg_link = ""

    detail = (
        "📥 <b>Movie Request (not found)</b>\n\n"
        f"👤 <b>User:</b> {name} ({uname})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"👥 <b>Group:</b> {chat_title}\n"
        f"🔎 <b>Query:</b> <code>{query}</code>\n"
        f"💬 chat=<code>{chat.id}</code> msg=<code>{message.id}</code>\n"
    )
    if msg_link:
        detail += f"🔗 <a href=\"{msg_link}\">Open message</a>\n"
    detail += (
        "\n➕ Movie add karo, phir <b>is message pe reply</b> karke "
        "<code>/dn</code> bhejo — bot group me user ke message pe reply karega."
    )

    _purge_old()
    for admin_id in targets:
        try:
            # 1) try forward original (keeps forward tag)
            try:
                await client.forward_messages(
                    chat_id=admin_id,
                    from_chat_id=chat.id,
                    message_ids=message.id,
                )
            except Exception as fe:
                logger.debug("dn forward to %s: %s", admin_id, fe)

            # 2) detail message (this is what admin replies /dn on)
            sent = await client.send_message(
                admin_id,
                detail,
                disable_web_page_preview=True,
            )
            if sent:
                _PENDING[sent.id] = {
                    "ts": time.time(),
                    "chat_id": chat.id,
                    "user_msg_id": message.id,
                    "user_id": user.id,
                    "query": query,
                    "admin_id": admin_id,
                }
                logger.info(
                    "dn notify admin=%s query=%r user=%s chat=%s msg=%s",
                    admin_id, query, user.id, chat.id, message.id,
                )
        except Exception as e:
            logger.warning("dn notify admin %s failed: %s", admin_id, e)


def register_dn_command(bot: Client):
    """Register /dn — reply to request notify in bot PM (or anywhere)."""

    @bot.on_message(
        filters.command(["dn", "done_notify", "reqdone"])
        & (filters.private | filters.group),
        group=-3,
    )
    async def dn_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            if not await _is_admin_user(client, message.from_user.id, getattr(message.chat, "id", None)):
                return

            reply = message.reply_to_message
            if not reply:
                try:
                    await message.reply_text(
                        "Usage: Movie request notify pe <b>reply</b> karke "
                        "<code>/dn</code> bhejo.\n"
                        "(Jo message bot ne 'Movie Request (not found)' ke naam se bheja tha.)",
                        quote=True,
                    )
                except Exception:
                    pass
                return

            _purge_old()
            meta = _PENDING.get(reply.id)

            # Also allow: admin replied /dn in group directly on user message (fallback)
            if not meta and message.chat and message.chat.type in (
                ChatType.GROUP, ChatType.SUPERGROUP
            ):
                meta = {
                    "chat_id": message.chat.id,
                    "user_msg_id": reply.id,
                    "user_id": getattr(reply.from_user, "id", None),
                    "query": "",
                }

            if not meta:
                try:
                    await message.reply_text(
                        "Is message se koi pending request link nahi mili.\n"
                        "Sirf bot wale <b>Movie Request</b> notify pe reply karke /dn use karo.",
                        quote=True,
                    )
                except Exception:
                    pass
                return

            chat_id = int(meta["chat_id"])
            user_msg_id = int(meta["user_msg_id"])
            query = meta.get("query") or ""

            # delete admin /dn command
            try:
                await message.delete()
            except Exception:
                pass

            text = DN_GROUP_TEXT
            if query:
                text = (
                    f"✅ <b>{query}</b> ab available hai.\n"
                    "Group me dubara search karo."
                )

            sent = None
            try:
                sent = await client.send_message(
                    chat_id,
                    text,
                    reply_to_message_id=user_msg_id,
                )
            except Exception as e:
                logger.warning("dn group reply failed: %s", e)
                try:
                    sent = await client.send_message(chat_id, text)
                except Exception as e2:
                    logger.error("dn group send failed: %s", e2)

            # clear this pending (and any duplicates for same user msg)
            _PENDING.pop(reply.id, None)
            for k, v in list(_PENDING.items()):
                if v.get("chat_id") == chat_id and v.get("user_msg_id") == user_msg_id:
                    _PENDING.pop(k, None)

            # confirm to admin in PM if command was in private
            if message.chat and message.chat.type == ChatType.PRIVATE:
                try:
                    ok = "✅ Group me user ko reply bhej diya." if sent else "⚠️ Group reply fail."
                    await client.send_message(message.chat.id, ok)
                except Exception:
                    pass

            logger.info(
                "dn done chat=%s user_msg=%s query=%r by=%s",
                chat_id, user_msg_id, query, message.from_user.id,
            )
        except Exception as e:
            logger.exception("dn_handler: %s", e)
