# ==============================================================================
# handlers/bot_commands/nrls_cmd.py
# Command: /nrls  (aliases: /nrl, /notreleased)
# Admin replies to a user message →
#   1) deletes the admin's command message
#   2) bot khud movie release date dhoondhta hai (TMDB/OMDb/AI/Wiki)
#   3) replies: movie abhi release nahi hui + expected release date
#   4) Google "check release date" button
# Without reply-to: does nothing.
# ==============================================================================

import asyncio
import logging
import urllib.parse

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from configs import Config

logger = logging.getLogger("NrlsCmd")

# (chat_id, user_message_id) -> [bot_reply_message_ids]
_NRLS_REPLY_MAP: dict = {}

_WATCH_INTERVAL = 3
_WATCH_MAX_CHECKS = 200


async def _is_authorized(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    user_id = message.from_user.id

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

    try:
        if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            member = await client.get_chat_member(message.chat.id, user_id)
            st = member.status
            if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
            st_s = str(getattr(st, "value", st) or st).lower()
            if any(x in st_s for x in ("administrator", "owner", "creator")):
                return True
    except Exception as e:
        logger.debug("nrls admin check: %s", e)

    return False


async def _delete_bot_replies(client: Client, chat_id: int, bot_ids: list):
    if not bot_ids:
        return
    try:
        await client.delete_messages(chat_id, bot_ids)
    except Exception:
        for mid in bot_ids:
            try:
                await client.delete_messages(chat_id, mid)
            except Exception:
                pass


def _user_msg_gone(msg) -> bool:
    if msg is None:
        return True
    if isinstance(msg, list):
        if not msg:
            return True
        msg = msg[0]
    if getattr(msg, "empty", False):
        return True
    if not getattr(msg, "id", None):
        return True
    return False


async def _watch_user_msg(client: Client, chat_id: int, user_msg_id: int, bot_msg_id: int):
    key = (int(chat_id), int(user_msg_id))
    try:
        for _ in range(_WATCH_MAX_CHECKS):
            await asyncio.sleep(_WATCH_INTERVAL)
            try:
                msg = await client.get_messages(chat_id, user_msg_id)
            except Exception:
                msg = None
            if _user_msg_gone(msg):
                bot_ids = _NRLS_REPLY_MAP.pop(key, None) or [int(bot_msg_id)]
                await _delete_bot_replies(client, chat_id, list(bot_ids))
                return
    except Exception as e:
        logger.debug("nrls watch: %s", e)


def _extract_query(target: Message) -> str:
    query = (target.text or target.caption or "").strip()
    if query.startswith("/"):
        parts = query.split(maxsplit=1)
        query = parts[1] if len(parts) > 1 else query
    return query.strip()


async def _lookup_release(query: str) -> dict | None:
    """TMDB/OMDb/AI/Wiki se best match + release date nikaalo."""
    if not query or len(query) < 2:
        return None
    try:
        from handlers.tmdb_suggest import multi_source_search, is_unreleased
        items = await multi_source_search(query, limit=5)
    except Exception as e:
        logger.warning("nrls multi_source: %s", e)
        return None
    if not items:
        return None

    # Prefer unreleased with a date, else first with any release_date/year
    best = None
    for it in items:
        if is_unreleased(it) and (it.get("release_date") or it.get("year")):
            best = it
            break
    if not best:
        for it in items:
            if it.get("release_date") or it.get("year"):
                best = it
                break
    if not best:
        best = items[0]
    return best


def register_nrls_command(bot: Client):
    """Register /nrls — not released + auto release date."""

    @bot.on_message(
        filters.command(["nrls", "nrl", "notreleased"])
        & (filters.group | filters.private),
        group=-3,
    )
    async def nrls_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            if not await _is_authorized(client, message):
                return

            target = message.reply_to_message
            if not target:
                try:
                    await message.delete()
                except Exception:
                    pass
                return

            try:
                await message.delete()
            except Exception as e:
                logger.warning("nrls: delete cmd failed: %s", e)

            query = _extract_query(target)
            item = await _lookup_release(query)

            if item:
                try:
                    from handlers.tmdb_suggest import (
                        build_unreleased_text,
                        build_unreleased_keyboard,
                    )
                    text = build_unreleased_text(query or item.get("title") or "Movie", item)
                    markup = build_unreleased_keyboard(item)
                except Exception as e:
                    logger.warning("nrls build text: %s", e)
                    title = item.get("title") or query or "Movie"
                    year = item.get("year") or ""
                    rd = item.get("release_date") or year or "TBA"
                    text = (
                        f"⏳ <b>{title}"
                        + (f" ({year})" if year else "")
                        + "</b>\n\n"
                        f"Sorry, ye movie <b>abhi release nahi hui</b>.\n"
                        f"Expected release: <b>{rd}</b>\n\n"
                        f"Release hone ke baad hi yahan milegi."
                    )
                    q = f"{title} {year} release date".strip()
                    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
                    markup = InlineKeyboardMarkup(
                        [[InlineKeyboardButton("📅 Check release date", url=url)]]
                    )
            else:
                # Date na mili — generic message + Google with user text
                label = query or "Movie"
                text = (
                    f"⏳ <b>{label}</b>\n\n"
                    f"Sorry, ye movie <b>abhi release nahi hui</b>.\n"
                    f"Expected release: <b>TBA</b>\n\n"
                    f"Release hone ke baad hi yahan milegi."
                )
                q = f"{label} release date"
                url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
                markup = InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📅 Check release date", url=url)]]
                )

            bot_msg = None
            try:
                bot_msg = await client.send_message(
                    chat_id=message.chat.id,
                    text=text,
                    reply_to_message_id=target.id,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning("nrls: send failed: %s", e)
                try:
                    bot_msg = await target.reply_text(
                        text,
                        quote=True,
                        reply_markup=markup,
                        disable_web_page_preview=True,
                    )
                except Exception as e2:
                    logger.warning("nrls: fallback send failed: %s", e2)

            if bot_msg and target:
                try:
                    chat_id = int(message.chat.id)
                    user_mid = int(target.id)
                    bot_mid = int(bot_msg.id)
                    key = (chat_id, user_mid)
                    _NRLS_REPLY_MAP.setdefault(key, [])
                    if bot_mid not in _NRLS_REPLY_MAP[key]:
                        _NRLS_REPLY_MAP[key].append(bot_mid)
                    asyncio.create_task(
                        _watch_user_msg(client, chat_id, user_mid, bot_mid)
                    )
                except Exception as e:
                    logger.debug("nrls map/watch: %s", e)

        except Exception as e:
            logger.exception("nrls_handler: %s", e)

    @bot.on_deleted_messages()
    async def nrls_on_deleted(client: Client, messages):
        try:
            if not messages:
                return
            for m in messages:
                try:
                    chat = getattr(m, "chat", None)
                    chat_id = getattr(chat, "id", None) if chat else None
                    msg_id = getattr(m, "id", None)
                    if chat_id is None or msg_id is None:
                        continue
                    key = (int(chat_id), int(msg_id))
                    bot_ids = _NRLS_REPLY_MAP.pop(key, None)
                    if bot_ids:
                        await _delete_bot_replies(client, int(chat_id), list(bot_ids))
                except Exception:
                    pass
        except Exception:
            pass

    logger.info("nrls command registered (/nrls /nrl /notreleased)")
