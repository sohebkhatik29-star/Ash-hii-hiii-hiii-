# ==============================================================================
# handlers/bot_commands/dn_cmd.py
# Movie not-found → notify admin; /dn or /done on that notify → group reply
# ==============================================================================

import logging
import re
import time
from pyrogram import Client, filters, StopPropagation
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DnCmd")

_PENDING: dict = {}
_PENDING_TTL = 48 * 3600
_NOT_FOUND_MSG: dict = {}
# (chat_id, user_msg_id) -> [bot_reply_msg_ids]  — user msg delete → bot replies delete
_BOT_REPLIES: dict = {}
# bot_msg_id -> (chat_id, user_msg_id) for reverse cleanup
_BOT_REPLY_TO_USER: dict = {}
_QUERY_INDEX: dict = {}

DN_GROUP_TEXT = (
    "✅ <b>Your requested movie is now available.</b>\n"
    "Please search again / check the group."
)
DONE_GROUP_TEXT = "Your file has been uploaded successfully. Please check it."


def _purge_old():
    now = time.time()
    for k, v in list(_PENDING.items()):
        if now - v.get("ts", 0) > _PENDING_TTL:
            q = (v.get("query") or "").strip().lower()
            _PENDING.pop(k, None)
            if q and q in _QUERY_INDEX:
                _QUERY_INDEX[q] = [x for x in _QUERY_INDEX[q] if x != k]
                if not _QUERY_INDEX[q]:
                    _QUERY_INDEX.pop(q, None)


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


def _meta_from_pending_by_query(reply: Message, admin_id: int = None):
    if not reply:
        return None
    body = (reply.text or reply.caption or "").strip()
    if not body or len(body) > 120:
        return None
    if body.startswith("/") or "Movie Request" in body or "chat=" in body:
        return None
    q = body.lower().strip()
    q2 = re.sub(r"[^\w\s]", "", q).strip()
    _purge_old()
    keys = list(_QUERY_INDEX.get(q, []))
    if q2 and q2 != q:
        keys.extend(_QUERY_INDEX.get(q2, []))
    for k in reversed(keys):
        meta = _PENDING.get(k)
        if not meta:
            continue
        if admin_id and meta.get("admin_id") and meta["admin_id"] != admin_id:
            continue
        out = dict(meta)
        if not out.get("not_found_msg_id"):
            out["not_found_msg_id"] = _NOT_FOUND_MSG.get(
                (out["chat_id"], out["user_msg_id"])
            )
        return out
    for k, meta in sorted(
        _PENDING.items(), key=lambda x: x[1].get("ts", 0), reverse=True
    ):
        mq = (meta.get("query") or "").strip().lower()
        if mq == q or (q2 and mq == q2):
            if admin_id and meta.get("admin_id") and meta["admin_id"] != admin_id:
                continue
            out = dict(meta)
            if not out.get("not_found_msg_id"):
                out["not_found_msg_id"] = _NOT_FOUND_MSG.get(
                    (out["chat_id"], out["user_msg_id"])
                )
            return out
    return None


def resolve_request_meta(reply: Message, admin_id: int = None):
    """Sync: pending → text → forward → query soft-match."""
    if not reply:
        return None
    _purge_old()
    meta = _PENDING.get(reply.id)
    if meta:
        return meta
    body = reply.text or reply.caption or ""
    meta = _parse_meta_from_text(body)
    if meta:
        key = (meta["chat_id"], meta["user_msg_id"])
        if not meta.get("not_found_msg_id"):
            meta["not_found_msg_id"] = _NOT_FOUND_MSG.get(key)
        return meta
    meta = _meta_from_forward(reply)
    if meta:
        key = (meta["chat_id"], meta["user_msg_id"])
        meta["not_found_msg_id"] = _NOT_FOUND_MSG.get(key)
        return meta
    return _meta_from_pending_by_query(reply, admin_id=admin_id)


async def resolve_request_meta_async(
    client: Client,
    reply: Message,
    admin_id: int = None,
    private_chat_id: int = None,
):
    """
    Full resolve: sync paths + private chat history scan for Movie Request card.
    Fixes: reply on forward (movie name only) after bot restart.
    """
    meta = resolve_request_meta(reply, admin_id=admin_id)
    if meta:
        return meta

    if not private_chat_id or not reply:
        return None

    hint = (reply.text or reply.caption or "").strip().lower()
    if hint.startswith("/"):
        hint = ""
    hint2 = re.sub(r"[^\w\s]", "", hint).strip() if hint else ""

    try:
        async for m in client.get_chat_history(private_chat_id, limit=40):
            body = m.text or m.caption or ""
            if not body:
                continue
            if "chat=" not in body and "msg=" not in body:
                continue
            if "Movie Request" not in body and "not found" not in body.lower():
                # still allow pure meta lines
                if "Query:" not in body:
                    continue
            parsed = _parse_meta_from_text(body)
            if not parsed:
                continue
            # if we have a short query hint (from forward), prefer matching cards
            if hint and len(hint) <= 80:
                pq = (parsed.get("query") or "").strip().lower()
                pq2 = re.sub(r"[^\w\s]", "", pq).strip()
                if pq and pq != hint and pq2 != hint2 and hint not in pq and pq not in hint:
                    continue
            key = (parsed["chat_id"], parsed["user_msg_id"])
            if not parsed.get("not_found_msg_id"):
                parsed["not_found_msg_id"] = _NOT_FOUND_MSG.get(key)
            logger.info(
                "dn history-scan matched chat=%s msg=%s q=%r hint=%r",
                parsed["chat_id"],
                parsed["user_msg_id"],
                parsed.get("query"),
                hint,
            )
            return parsed
    except Exception as e:
        logger.warning("dn history-scan failed: %s", e)

    return None



_WATCH_INTERVAL = 2  # seconds between polls
_WATCH_MAX = 200     # ~10 min coverage
_WATCHING: set = set()  # keys already being polled


def track_bot_reply(chat_id: int, user_msg_id: int, bot_msg_id: int, client: Client = None):
    """Link bot reply to user message so delete cascades both ways."""
    try:
        chat_id = int(chat_id)
        user_msg_id = int(user_msg_id)
        bot_msg_id = int(bot_msg_id)
        key = (chat_id, user_msg_id)
        lst = _BOT_REPLIES.setdefault(key, [])
        if bot_msg_id not in lst:
            lst.append(bot_msg_id)
        _BOT_REPLY_TO_USER[bot_msg_id] = (chat_id, user_msg_id)
        # start poll-watch (bots often miss deleted_messages updates)
        if client is not None and key not in _WATCHING:
            _WATCHING.add(key)
            try:
                import asyncio
                asyncio.create_task(_poll_user_msg(client, chat_id, user_msg_id))
            except Exception as e:
                logger.debug("watch start: %s", e)
                _WATCHING.discard(key)
    except Exception as e:
        logger.debug("track_bot_reply: %s", e)


async def _poll_user_msg(client: Client, chat_id: int, user_msg_id: int):
    """Poll until user message is gone, then delete bot replies."""
    key = (int(chat_id), int(user_msg_id))
    try:
        import asyncio
        for i in range(_WATCH_MAX):
            await asyncio.sleep(_WATCH_INTERVAL)
            if key not in _BOT_REPLIES and key not in _NOT_FOUND_MSG:
                return
            gone = False
            try:
                msg = await client.get_messages(int(chat_id), int(user_msg_id))
                # Pyrogram: deleted → empty Message or None
                if msg is None:
                    gone = True
                elif getattr(msg, "empty", False):
                    gone = True
                elif getattr(msg, "id", None) is None:
                    gone = True
            except Exception as e:
                # MESSAGE_ID_INVALID / not found → gone
                err = str(e).lower()
                if "invalid" in err or "not found" in err or "empty" in err or "id" in err:
                    gone = True
                else:
                    logger.debug("poll get_messages %s/%s: %s", chat_id, user_msg_id, e)
            if gone:
                logger.info("poll: user msg %s gone in %s → cascade bot replies", user_msg_id, chat_id)
                await cascade_delete_for_user_msg(client, chat_id, user_msg_id)
                return
    finally:
        _WATCHING.discard(key)


async def cascade_delete_for_user_msg(client: Client, chat_id: int, user_msg_id: int):
    """User message deleted → delete all tracked bot replies."""
    key = (int(chat_id), int(user_msg_id))
    bot_ids = list(_BOT_REPLIES.pop(key, None) or [])
    nf = _NOT_FOUND_MSG.pop(key, None)
    if nf and int(nf) not in [int(x) for x in bot_ids]:
        bot_ids.append(int(nf))
    if not bot_ids:
        logger.debug("cascade user→bot: no bot replies for %s/%s", chat_id, user_msg_id)
        return
    for bid in bot_ids:
        _BOT_REPLY_TO_USER.pop(int(bid), None)
    # batch delete all bot replies at once
    try:
        await client.delete_messages(int(chat_id), [int(b) for b in bot_ids])
        logger.info("cascade: deleted bot replies %s in %s (user %s gone)", bot_ids, chat_id, user_msg_id)
    except Exception as e:
        logger.warning("cascade batch delete failed %s: %s — trying one by one", bot_ids, e)
        for bid in bot_ids:
            try:
                await client.delete_messages(int(chat_id), int(bid))
                logger.info("cascade: deleted bot reply %s in %s (user %s gone)", bid, chat_id, user_msg_id)
            except Exception as e2:
                logger.warning("cascade delete bot %s failed: %s", bid, e2)


async def cascade_delete_for_bot_reply(client: Client, chat_id: int, bot_msg_id: int):
    """Bot reply deleted → delete original user message + sibling bot replies."""
    info = _BOT_REPLY_TO_USER.pop(int(bot_msg_id), None)
    if not info:
        logger.debug("cascade bot→user: no mapping for bot_msg %s", bot_msg_id)
        return
    c_id, user_msg_id = info
    chat_id = int(c_id)
    user_msg_id = int(user_msg_id)
    key = (chat_id, user_msg_id)
    siblings = list(_BOT_REPLIES.pop(key, []) or [])
    to_del = []
    for bid in siblings:
        _BOT_REPLY_TO_USER.pop(int(bid), None)
        if int(bid) != int(bot_msg_id):
            to_del.append(int(bid))
    # delete sibling bot replies + user message together
    to_del.append(user_msg_id)
    try:
        await client.delete_messages(chat_id, to_del)
        logger.info(
            "cascade: deleted user %s + siblings %s in %s (bot reply %s gone)",
            user_msg_id, [x for x in to_del if x != user_msg_id], chat_id, bot_msg_id,
        )
    except Exception as e:
        logger.warning("cascade bot→user batch failed: %s — one by one", e)
        for mid in to_del:
            try:
                await client.delete_messages(chat_id, int(mid))
                logger.info("cascade: deleted %s in %s (bot reply %s gone)", mid, chat_id, bot_msg_id)
            except Exception as e2:
                logger.warning("cascade delete %s failed: %s", mid, e2)



async def complete_request_in_group(
    client: Client,
    meta: dict,
    *,
    text: str = None,
    prefer_done_text: bool = False,
) -> bool:
    chat_id = int(meta["chat_id"])
    user_msg_id = int(meta["user_msg_id"])
    query = (meta.get("query") or "").strip()
    nf_id = meta.get("not_found_msg_id")

    if text is None:
        if prefer_done_text:
            text = DONE_GROUP_TEXT
        elif query:
            text = (
                f"✅ <b>{query}</b> ab available hai.\n"
                "Group me dubara search karo."
            )
        else:
            text = DN_GROUP_TEXT

    # delete "nahi mila"
    if nf_id:
        try:
            await client.delete_messages(chat_id, int(nf_id))
            logger.info("dn: deleted not-found msg %s in %s", nf_id, chat_id)
        except Exception as e:
            logger.warning("dn: delete not-found %s failed: %s", nf_id, e)
    else:
        key = (chat_id, user_msg_id)
        cached = _NOT_FOUND_MSG.pop(key, None)
        if cached:
            try:
                await client.delete_messages(chat_id, int(cached))
                logger.info("dn: deleted cached not-found %s", cached)
            except Exception as e:
                logger.warning("dn: delete cached nf failed: %s", e)

    sent = None
    try:
        sent = await client.send_message(
            chat_id,
            text,
            reply_to_message_id=user_msg_id,
        )
        logger.info("dn: group reply ok chat=%s to msg=%s", chat_id, user_msg_id)
    except Exception as e:
        logger.warning("dn group reply_to failed %s/%s: %s", chat_id, user_msg_id, e)
        try:
            sent = await client.send_message(chat_id, text)
            logger.info("dn: group plain send ok chat=%s", chat_id)
        except Exception as e2:
            logger.error("dn group send failed: %s", e2)
            return False

    if sent and getattr(sent, "id", None):
        track_bot_reply(chat_id, user_msg_id, sent.id, client=client)

    for k, v in list(_PENDING.items()):
        if v.get("chat_id") == chat_id and v.get("user_msg_id") == user_msg_id:
            _PENDING.pop(k, None)
            qq = (v.get("query") or "").strip().lower()
            if qq and qq in _QUERY_INDEX:
                _QUERY_INDEX[qq] = [x for x in _QUERY_INDEX[qq] if x != k]
                if not _QUERY_INDEX[qq]:
                    _QUERY_INDEX.pop(qq, None)
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
        track_bot_reply(chat.id, message.id, int(not_found_msg_id), client=client)

    # Single card — no separate forward needed for resolve
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
        detail += f'🔗 <a href="{msg_link}">Open message</a>\n'
    detail += (
        "\n➕ Movie add karo, phir <b>is message pe reply</b> karke "
        "<code>/dn</code> ya <code>/done</code> bhejo — "
        "bot group me user ke message pe reply karega + nahi-mila delete."
    )

    _purge_old()
    for admin_id in targets:
        try:
            meta_base = {
                "ts": time.time(),
                "chat_id": chat.id,
                "user_msg_id": message.id,
                "user_id": user.id,
                "query": query,
                "admin_id": admin_id,
                "not_found_msg_id": int(not_found_msg_id)
                if not_found_msg_id
                else None,
            }
            # optional forward of original user message — track its PM id too
            try:
                fwd = await client.forward_messages(
                    chat_id=admin_id,
                    from_chat_id=chat.id,
                    message_ids=message.id,
                )
                # Pyrogram may return Message or list
                fwd_list = fwd if isinstance(fwd, (list, tuple)) else ([fwd] if fwd else [])
                for fm in fwd_list:
                    fid = getattr(fm, "id", None)
                    if fid:
                        _PENDING[int(fid)] = dict(meta_base)
                        logger.debug("dn tracked forward pm_msg=%s admin=%s", fid, admin_id)
            except Exception as fe:
                logger.debug("dn forward %s: %s", admin_id, fe)

            sent = await client.send_message(
                admin_id, detail, disable_web_page_preview=True
            )
            if sent:
                _PENDING[sent.id] = dict(meta_base)
                ql = query.lower()
                _QUERY_INDEX.setdefault(ql, []).append(sent.id)
                logger.info(
                    "dn notify admin=%s q=%r chat=%s msg=%s nf=%s pm_card=%s",
                    admin_id,
                    query,
                    chat.id,
                    message.id,
                    not_found_msg_id,
                    sent.id,
                )
        except Exception as e:
            logger.warning("dn notify admin %s: %s", admin_id, e)


def register_dn_command(bot: Client):
    @bot.on_message(
        filters.command(["dn", "done", "done_notify", "reqdone"])
        & (filters.private | filters.group),
        group=-5,
    )
    async def dn_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            uid = message.from_user.id
            if not await _is_admin_user(
                client, uid, getattr(message.chat, "id", None)
            ):
                return

            reply = message.reply_to_message
            if not reply:
                await message.reply_text(
                    "Usage: <b>📥 Movie Request (not found)</b> card pe "
                    "reply karke <code>/dn</code> ya <code>/done</code> bhejo.",
                    quote=True,
                )
                return

            priv_id = (
                message.chat.id
                if message.chat and message.chat.type == ChatType.PRIVATE
                else None
            )
            meta = await resolve_request_meta_async(
                client, reply, admin_id=uid, private_chat_id=priv_id
            )

            # Group /done without request meta → leave for traditional done_cmd
            cmd = (message.command or [""])[0].lower() if message.command else ""
            if not meta and message.chat and message.chat.type in (
                ChatType.GROUP,
                ChatType.SUPERGROUP,
            ):
                if cmd == "done":
                    return  # traditional /done in group (done_cmd)
                meta = {
                    "chat_id": message.chat.id,
                    "user_msg_id": reply.id,
                    "query": (reply.text or reply.caption or "").strip()[:80],
                    "not_found_msg_id": _NOT_FOUND_MSG.get(
                        (message.chat.id, reply.id)
                    ),
                }

            if not meta:
                # /done in private without meta → leave for done_cmd local path
                if cmd == "done" and message.chat and message.chat.type == ChatType.PRIVATE:
                    return
                await message.reply_text(
                    "Is message se request nahi samajh aayi.\n\n"
                    "Bot wale <b>📥 Movie Request (not found)</b> card pe "
                    "(jisme <code>chat=… msg=… nfmsg=…</code> likha ho) "
                    "<b>reply</b> karke <code>/dn</code> ya <code>/done</code> bhejo.\n"
                    "Forward (sirf movie name) pe bhi try kar sakte ho — "
                    "bot recent request dhoondhega.",
                    quote=True,
                )
                return

            try:
                await message.delete()
            except Exception:
                pass

            ok = await complete_request_in_group(client, meta, prefer_done_text=True)
            if message.chat and message.chat.type == ChatType.PRIVATE:
                try:
                    await client.send_message(
                        message.chat.id,
                        "✅ Group me user ko reply + nahi-mila delete."
                        if ok
                        else "⚠️ Group reply fail — bot group me admin + message permission check karo.",
                    )
                except Exception:
                    pass
            logger.info(
                "dn ok=%s chat=%s msg=%s by=%s",
                ok,
                meta.get("chat_id"),
                meta.get("user_msg_id"),
                uid,
            )
            raise StopPropagation
        except StopPropagation:
            raise
        except Exception as e:
            logger.exception("dn_handler: %s", e)

    
    @bot.on_deleted_messages(group=-1)
    async def dn_on_deleted(client: Client, messages):
        """Group cascade + Bot-PM card/forward delete → group cascade."""
        try:
            if not messages:
                return
            items = list(messages) if not isinstance(messages, (list, tuple)) else messages
            # parent-level chat / message_ids (common for channel/private deletes)
            parent_chat = getattr(messages, "chat", None)
            parent_chat_id = getattr(parent_chat, "id", None) if parent_chat else None
            parent_ids = getattr(messages, "message_ids", None)
            if parent_ids and parent_chat_id is not None:
                for mid in parent_ids:
                    try:
                        await _handle_deleted_id(client, int(parent_chat_id), int(mid))
                    except Exception as e:
                        logger.debug("dn_on_deleted parent id: %s", e)
            for m in items:
                try:
                    chat = getattr(m, "chat", None)
                    chat_id = getattr(chat, "id", None) if chat else getattr(m, "chat_id", None)
                    if chat_id is None:
                        chat_id = parent_chat_id
                    msg_id = getattr(m, "id", None) or getattr(m, "message_id", None)
                    if msg_id is None and hasattr(m, "message_ids"):
                        for mid in m.message_ids:
                            # even without chat_id, try PM pending lookup
                            await _handle_deleted_id(client, chat_id if chat_id is not None else 0, int(mid))
                        continue
                    if msg_id is None:
                        continue
                    # allow chat_id=0 so PM pending path still runs via msg_id in _PENDING
                    await _handle_deleted_id(
                        client,
                        int(chat_id) if chat_id is not None else 0,
                        int(msg_id),
                    )
                except Exception as e:
                    logger.debug("dn_on_deleted item: %s", e)
        except Exception as e:
            logger.debug("dn_on_deleted: %s", e)

    async def _handle_deleted_id(client: Client, chat_id: int, msg_id: int):
        if chat_id is None or msg_id is None:
            return
        chat_id = int(chat_id)
        msg_id = int(msg_id)

        # ---- A) Group: user msg deleted → bot replies ----
        if (chat_id, msg_id) in _BOT_REPLIES or (chat_id, msg_id) in _NOT_FOUND_MSG:
            await cascade_delete_for_user_msg(client, chat_id, msg_id)
            return

        # ---- B) Group: bot reply deleted → user msg ----
        if msg_id in _BOT_REPLY_TO_USER:
            await cascade_delete_for_bot_reply(client, chat_id, msg_id)
            return

        # ---- C) Bot PM: admin deleted Movie Request card / forward ----
        #     → delete user message + all bot replies in the GROUP
        meta = _PENDING.pop(msg_id, None)
        if meta:
            # clean query index
            q = (meta.get("query") or "").strip().lower()
            if q and q in _QUERY_INDEX:
                _QUERY_INDEX[q] = [x for x in _QUERY_INDEX[q] if x != msg_id]
                if not _QUERY_INDEX[q]:
                    _QUERY_INDEX.pop(q, None)
            # also drop any other PM keys pointing to same group request
            g_chat = int(meta["chat_id"])
            u_msg = int(meta["user_msg_id"])
            for k, v in list(_PENDING.items()):
                try:
                    if int(v.get("chat_id", 0)) == g_chat and int(v.get("user_msg_id", 0)) == u_msg:
                        _PENDING.pop(k, None)
                except Exception:
                    pass
            logger.info(
                "pm-delete cascade: admin deleted card/fwd %s → group %s user_msg %s",
                msg_id, g_chat, u_msg,
            )
            # delete bot replies (nahi-mila / done / results)
            await cascade_delete_for_user_msg(client, g_chat, u_msg)
            # delete original user message in group
            try:
                await client.delete_messages(g_chat, u_msg)
                logger.info("pm-delete: deleted user msg %s in group %s", u_msg, g_chat)
            except Exception as e:
                logger.warning("pm-delete user msg %s failed: %s", u_msg, e)
            return


    logger.info("/dn registered")
