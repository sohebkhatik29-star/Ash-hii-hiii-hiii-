# ==============================================================================
# handlers/bot_commands/dn_cmd.py
# Movie not-found → notify admin; /dn or /done on that notify → group reply
# ==============================================================================

import logging
import re
import time
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DnCmd")

_PENDING: dict = {}
_PENDING_TTL = 48 * 3600
# user_msg key "(chat_id, user_msg_id)" -> not_found bot msg id
_NOT_FOUND_MSG: dict = {}

DN_GROUP_TEXT = (
    "✅ <b>Your requested movie is now available.</b>\n"
    "Please search again / check the group."
)
DONE_GROUP_TEXT = "Your file has been uploaded successfully. Please check it."


def _purge_old():
    now = time.time()
    for k, v in list(_PENDING.items()):
        if now - v.get("ts", 0) > _PENDING_TTL:
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


def _parse_meta_from_text(text: str):
    if not text:
        return None
    # strip html tags for safer parse
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

    m = re.search(r"chat\s*=\s*(-?\d+)\s+msg\s*=\s*(\d+)", plain, re.I)
    if not m:
        m = re.search(r"chat[=:\s]+(-?\d+).*?msg[=:\s]+(\d+)", plain, re.I | re.S)
    if not m:
        return None
    chat_id = int(m.group(1))
    user_msg_id = int(m.group(2))
    query = ""
    qm = re.search(r"Query:\s*(?:<code>)?([^\n<]+)", text, re.I)
    if not qm:
        qm = re.search(r"Query:\s*([^\n]+)", plain, re.I)
    if qm:
        query = qm.group(1).strip()
    nf = None
    nfm = re.search(r"nfmsg\s*=\s*(\d+)", plain, re.I)
    if nfm:
        nf = int(nfm.group(1))
    return {
        "chat_id": chat_id,
        "user_msg_id": user_msg_id,
        "query": query,
        "user_id": None,
        "not_found_msg_id": nf,
    }


def _meta_from_forward(reply: Message):
    if not reply:
        return None
    fchat = getattr(reply, "forward_from_chat", None)
    fmid = getattr(reply, "forward_from_message_id", None)
    if fchat is not None and fmid:
        query = (reply.text or reply.caption or "").strip()
        return {
            "chat_id": int(fchat.id),
            "user_msg_id": int(fmid),
            "query": query,
            "user_id": getattr(getattr(reply, "forward_from", None), "id", None),
            "not_found_msg_id": None,
        }
    origin = getattr(reply, "forward_origin", None)
    if origin is not None:
        try:
            chat = getattr(origin, "chat", None)
            mid = getattr(origin, "message_id", None)
            if chat is not None and mid:
                query = (reply.text or reply.caption or "").strip()
                return {
                    "chat_id": int(chat.id),
                    "user_msg_id": int(mid),
                    "query": query,
                    "user_id": None,
                    "not_found_msg_id": None,
                }
        except Exception:
            pass
    return None


def resolve_request_meta(reply: Message):
    """Public: pending → text parse → forward. Used by /dn and /done."""
    if not reply:
        return None
    _purge_old()
    meta = _PENDING.get(reply.id)
    if meta:
        return meta
    body = reply.text or reply.caption or ""
    meta = _parse_meta_from_text(body)
    if meta:
        # merge not_found from map if missing
        key = (meta["chat_id"], meta["user_msg_id"])
        if not meta.get("not_found_msg_id"):
            meta["not_found_msg_id"] = _NOT_FOUND_MSG.get(key)
        return meta
    meta = _meta_from_forward(reply)
    if meta:
        key = (meta["chat_id"], meta["user_msg_id"])
        meta["not_found_msg_id"] = _NOT_FOUND_MSG.get(key)
        return meta
    return None


async def complete_request_in_group(
    client: Client,
    meta: dict,
    *,
    text: str = None,
    prefer_done_text: bool = False,
) -> bool:
    """
    Reply in group on original user message. Delete 'nahi mila' if known.
    Returns True if group message sent.
    """
    chat_id = int(meta["chat_id"])
    user_msg_id = int(meta["user_msg_id"])
    query = (meta.get("query") or "").strip()
    nf_id = meta.get("not_found_msg_id")

    if text is None:
        if prefer_done_text:
            text = DONE_GROUP_TEXT
        elif query:
            text = f"✅ <b>{query}</b> ab available hai.\nGroup me dubara search karo."
        else:
            text = DN_GROUP_TEXT

    # delete "nahi mila" bot message first
    if nf_id:
        try:
            await client.delete_messages(chat_id, int(nf_id))
            logger.info("dn: deleted not-found msg %s in %s", nf_id, chat_id)
        except Exception as e:
            logger.debug("dn: delete not-found failed: %s", e)
    else:
        key = (chat_id, user_msg_id)
        cached = _NOT_FOUND_MSG.pop(key, None)
        if cached:
            try:
                await client.delete_messages(chat_id, int(cached))
            except Exception:
                pass

    sent = None
    try:
        sent = await client.send_message(
            chat_id,
            text,
            reply_to_message_id=user_msg_id,
        )
    except Exception as e:
        logger.warning("dn group reply_to failed %s/%s: %s", chat_id, user_msg_id, e)
        try:
            sent = await client.send_message(chat_id, text)
        except Exception as e2:
            logger.error("dn group send failed: %s", e2)
            return False

    # clear pending for this request
    for k, v in list(_PENDING.items()):
        if v.get("chat_id") == chat_id and v.get("user_msg_id") == user_msg_id:
            _PENDING.pop(k, None)
    _NOT_FOUND_MSG.pop((chat_id, user_msg_id), None)
    return bool(sent)


async def notify_admin_movie_request(
    client: Client,
    message: Message,
    query: str,
    not_found_msg_id: int = None,
) -> None:
    if not message or not message.from_user or not message.chat:
        return
    user = message.from_user
    chat = message.chat
    query = (query or "").strip()
    if not query:
        return

    targets = _admin_targets()
    if not targets:
        logger.warning("dn: no admin targets")
        return

    uname = f"@{user.username}" if user.username else "—"
    name = (user.first_name or "") + (
        f" {user.last_name}" if user.last_name else ""
    )
    name = name.strip() or "User"
    chat_title = getattr(chat, "title", None) or str(chat.id)

    msg_link = ""
    try:
        if getattr(chat, "username", None):
            msg_link = f"https://t.me/{chat.username}/{message.id}"
        else:
            cid = str(chat.id)
            if cid.startswith("-100"):
                msg_link = f"https://t.me/c/{cid[4:]}/{message.id}"
    except Exception:
        pass

    if not_found_msg_id:
        _NOT_FOUND_MSG[(chat.id, message.id)] = int(not_found_msg_id)

    detail = (
        "📥 <b>Movie Request (not found)</b>\n\n"
        f"👤 <b>User:</b> {name} ({uname})\n"
        f"🆔 <code>{user.id}</code>\n"
        f"👥 <b>Group:</b> {chat_title}\n"
        f"🔎 <b>Query:</b> <code>{query}</code>\n"
        f"chat={chat.id} msg={message.id}"
    )
    if not_found_msg_id:
        detail += f" nfmsg={int(not_found_msg_id)}"
    detail += "\n"
    if msg_link:
        detail += f"🔗 <a href=\"{msg_link}\">Open message</a>\n"
    detail += (
        "\n➕ Movie add karo, phir <b>is message pe reply</b> karke "
        "<code>/dn</code> ya <code>/done</code> bhejo — "
        "bot group me user ke message pe reply karega."
    )

    _purge_old()
    for admin_id in targets:
        try:
            try:
                await client.forward_messages(
                    chat_id=admin_id,
                    from_chat_id=chat.id,
                    message_ids=message.id,
                )
            except Exception as fe:
                logger.debug("dn forward %s: %s", admin_id, fe)

            sent = await client.send_message(
                admin_id, detail, disable_web_page_preview=True
            )
            if sent:
                _PENDING[sent.id] = {
                    "ts": time.time(),
                    "chat_id": chat.id,
                    "user_msg_id": message.id,
                    "user_id": user.id,
                    "query": query,
                    "admin_id": admin_id,
                    "not_found_msg_id": int(not_found_msg_id) if not_found_msg_id else None,
                }
                logger.info(
                    "dn notify admin=%s q=%r chat=%s msg=%s nf=%s",
                    admin_id, query, chat.id, message.id, not_found_msg_id,
                )
        except Exception as e:
            logger.warning("dn notify admin %s: %s", admin_id, e)


def register_dn_command(bot: Client):
    @bot.on_message(
        filters.command(["dn", "done_notify", "reqdone"])
        & (filters.private | filters.group),
        group=-3,
    )
    async def dn_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            if not await _is_admin_user(
                client, message.from_user.id, getattr(message.chat, "id", None)
            ):
                return

            reply = message.reply_to_message
            if not reply:
                await message.reply_text(
                    "Usage: <b>Movie Request</b> card pe reply karke "
                    "<code>/dn</code> ya <code>/done</code> bhejo.",
                    quote=True,
                )
                return

            meta = resolve_request_meta(reply)

            # group: reply on user message
            if not meta and message.chat and message.chat.type in (
                ChatType.GROUP, ChatType.SUPERGROUP
            ):
                meta = {
                    "chat_id": message.chat.id,
                    "user_msg_id": reply.id,
                    "query": (reply.text or reply.caption or "").strip()[:80],
                    "not_found_msg_id": _NOT_FOUND_MSG.get(
                        (message.chat.id, reply.id)
                    ),
                }

            if not meta:
                await message.reply_text(
                    "Is message se request nahi samajh aayi.\n"
                    "Bot wale <b>Movie Request (not found)</b> card pe "
                    "reply karke <code>/dn</code> bhejo.",
                    quote=True,
                )
                return

            try:
                await message.delete()
            except Exception:
                pass

            ok = await complete_request_in_group(client, meta)
            if message.chat and message.chat.type == ChatType.PRIVATE:
                try:
                    await client.send_message(
                        message.chat.id,
                        "✅ Group me user ko reply + nahi-mila delete."
                        if ok
                        else "⚠️ Group reply fail — bot group me admin hai?",
                    )
                except Exception:
                    pass
            logger.info(
                "dn ok=%s chat=%s msg=%s by=%s",
                ok, meta.get("chat_id"), meta.get("user_msg_id"),
                message.from_user.id,
            )
        except Exception as e:
            logger.exception("dn_handler: %s", e)
