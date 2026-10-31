# ============================================================================
# handlers/channel_auto_index.py
# Auto-index: watch channel pe nayi movie files aate hi
#   - title+year se movie identify
#   - saari qualities save
#   - har movie ka alag group post (Pushpa 2021 vs Pushpa 2 2025)
# Sirf isi file me logic — dusre modules me change nahi.
# ============================================================================
from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Any, Optional, Tuple

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size

logger = logging.getLogger("ChannelAutoIndex")

# User-given watch channel (bot must be admin)
_DEFAULT_CH = "-1004397160844"
WATCH_CHANNEL_ID = int(
    __import__("os").environ.get("AUTO_INDEX_CHANNEL_ID")
    or getattr(Config, "AUTO_INDEX_CHANNEL_ID", None)
    or _DEFAULT_CH
)

# Same movie ki qualities collect karne ka wait (seconds)
SETTLE_SECONDS = int(__import__("os").environ.get("AUTO_INDEX_SETTLE_SEC", "25") or 25)

_pending_finalize: Dict[str, asyncio.Task] = {}
_key_to_movie: Dict[str, str] = {}
_lock = asyncio.Lock()
_stats = {"seen": 0, "saved": 0, "posted": 0, "errors": 0}


def _safe(text: Any, fallback: str = "") -> str:
    try:
        s = str(text if text is not None else fallback)
        return s.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")
    except Exception:
        return fallback


def _movie_key(title: str, year: Any) -> str:
    t = re.sub(r"[^\w\s]", "", (title or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    y = str(year or "")
    return f"{t}|{y}"


def _quality_key(meta: dict) -> str:
    q = (meta.get("quality") or "HD").strip()
    season = (meta.get("season") or "").strip()
    episode = (meta.get("episode") or "").strip()
    if episode:
        parts = [q]
        if season:
            parts.append(season)
        parts.append(episode)
        return " ".join(parts)
    return q or "HD"


async def _notify(bot: Client, text: str):
    """Owner / LOG_CHANNEL pe short status."""
    targets = []
    log_ch = getattr(Config, "LOG_CHANNEL", None)
    owner = getattr(Config, "BOT_OWNER", None)
    if log_ch:
        targets.append(int(log_ch))
    if owner:
        targets.append(int(owner))
    for t in targets:
        try:
            await bot.send_message(t, _safe(text)[:3500], disable_web_page_preview=True)
            return
        except Exception:
            continue


async def _copy_to_db(bot: Client, message: Message, caption_text: str = None) -> Tuple[Optional[str], Optional[int]]:
    media = message.document or message.video or message.audio or message.animation
    if not media:
        return None, None
    fname = getattr(media, "file_name", None) or ""
    cap = (caption_text or message.caption or fname or "").strip()
    if len(cap) > 1024:
        cap = cap[:1020] + "..."
    orig_fid = getattr(media, "file_id", None)
    db_ch = getattr(Config, "DB_CHANNEL", None)
    if not db_ch:
        return orig_fid, None
    try:
        copied = await message.copy(int(db_ch), caption=cap if cap else None)
        m = copied.document or copied.video or copied.audio or copied.animation
        return (getattr(m, "file_id", None) if m else orig_fid), getattr(copied, "id", None)
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            copied = await message.copy(int(db_ch), caption=cap if cap else None)
            m = copied.document or copied.video or copied.audio or copied.animation
            return (getattr(m, "file_id", None) if m else orig_fid), getattr(copied, "id", None)
        except Exception:
            return orig_fid, None
    except Exception as e:
        logger.warning("auto_index copy_to_db: %s", e)
        return orig_fid, None


async def _get_or_create_movie(title: str, year: Any, language: str = None) -> Optional[str]:
    title = _safe(title) or "Unknown"
    year_val = None
    try:
        if year is not None and str(year).isdigit():
            year_val = int(year)
    except Exception:
        year_val = None

    existing = None
    try:
        if hasattr(db, "find_movie_by_title_year"):
            existing = await db.find_movie_by_title_year(title, year=year_val)
        elif hasattr(db, "find_movie_by_title"):
            existing = await db.find_movie_by_title(title, year=year_val)
    except Exception as e:
        logger.debug("find movie: %s", e)
        existing = None

    if existing and existing.get("movie_id"):
        return existing["movie_id"]

    try:
        doc = await db.create_movie(
            title=title,
            normalized_title=(title or "").lower(),
            year=year_val,
            languages=[language] if language and language != "Unknown" else ["Hindi"],
        )
        return doc.get("movie_id") if doc else None
    except Exception as e:
        logger.error("create_movie: %s", e)
        return None


async def _post_or_update_group(bot: Client, movie_id: str) -> bool:
    from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons

    movie = await db.get_movie(movie_id)
    if not movie:
        return False
    target = getattr(Config, "MOVIE_GROUP_ID", None)
    if not target:
        logger.warning("MOVIE_GROUP_ID not set")
        return False

    card_text = build_final_movie_card_text(movie)
    buttons = build_final_movie_card_buttons(movie, for_group=True)
    poster_id = movie.get("poster_file_id")
    g_chat, g_msg = movie.get("group_chat_id"), movie.get("group_msg_id")

    if g_chat and g_msg:
        try:
            if poster_id:
                try:
                    await bot.edit_message_caption(
                        int(g_chat), int(g_msg),
                        caption=_safe(card_text),
                        reply_markup=buttons,
                    )
                    return True
                except Exception:
                    pass
            await bot.edit_message_text(
                int(g_chat), int(g_msg),
                text=_safe(card_text),
                reply_markup=buttons,
                disable_web_page_preview=True,
            )
            return True
        except Exception:
            pass

    sent = None
    try:
        if poster_id:
            try:
                sent = await bot.send_photo(
                    int(target),
                    photo=poster_id,
                    caption=_safe(card_text),
                    reply_markup=buttons,
                )
            except Exception:
                sent = None
        if not sent:
            sent = await bot.send_message(
                int(target),
                text=_safe(card_text),
                reply_markup=buttons,
                disable_web_page_preview=True,
            )
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            sent = await bot.send_message(
                int(target),
                text=_safe(card_text),
                reply_markup=buttons,
                disable_web_page_preview=True,
            )
        except Exception as e2:
            logger.warning("auto_index group post flood retry: %s", e2)
            return False
    except Exception as e:
        logger.warning("auto_index group post: %s", e)
        return False

    if sent:
        try:
            await db.update_movie_group_post(movie_id, sent.chat.id, sent.id)
            real_link = getattr(sent, "link", None)
            if real_link and "t.me" in str(real_link):
                try:
                    if getattr(db, "movies_col", None) is not None:
                        await db.movies_col.update_one(
                            {"movie_id": movie_id},
                            {"$set": {"group_post_link": str(real_link).strip()}},
                        )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("update_movie_group_post: %s", e)
        return True
    return False


async def _finalize_movie(bot: Client, movie_key: str, movie_id: str):
    try:
        await asyncio.sleep(SETTLE_SECONDS)
        async with _lock:
            task = _pending_finalize.get(movie_key)
            if task is not asyncio.current_task():
                return
            _pending_finalize.pop(movie_key, None)
        ok = await _post_or_update_group(bot, movie_id)
        movie = await db.get_movie(movie_id)
        title = (movie or {}).get("title") or movie_id
        qs = list(((movie or {}).get("qualities") or {}).keys())
        _stats["posted"] += 1 if ok else 0
        logger.info(
            "auto_index FINALIZE movie=%s title=%s qualities=%s posted=%s",
            movie_id, title, qs, ok,
        )
        await _notify(
            bot,
            f"✅ Auto-index POSTED\n"
            f"🎬 {title}\n"
            f"📦 Qualities: {', '.join(qs) if qs else '-'}\n"
            f"{'Group me post ho gaya' if ok else '⚠️ Group post fail — logs check karo'}",
        )
    except asyncio.CancelledError:
        return
    except Exception as e:
        _stats["errors"] += 1
        logger.exception("auto_index finalize: %s", e)


def _schedule_finalize(bot: Client, movie_key: str, movie_id: str):
    old = _pending_finalize.get(movie_key)
    if old and not old.done():
        old.cancel()
    _key_to_movie[movie_key] = movie_id
    _pending_finalize[movie_key] = asyncio.create_task(
        _finalize_movie(bot, movie_key, movie_id)
    )


async def _handle_channel_media(bot: Client, message: Message):
    if not message or message.chat is None:
        return

    chat_id = int(message.chat.id)
    _stats["seen"] += 1
    logger.info(
        "auto_index SEEN chat=%s msg=%s type=%s",
        chat_id, message.id, getattr(message.chat, "type", None),
    )

    if chat_id != int(WATCH_CHANNEL_ID):
        logger.info("auto_index skip wrong chat %s != %s", chat_id, WATCH_CHANNEL_ID)
        return

    media = message.document or message.video or message.audio or message.animation
    if not media:
        logger.info("auto_index skip non-media msg=%s", message.id)
        return

    file_name = getattr(media, "file_name", None) or ""
    file_size = int(getattr(media, "file_size", 0) or 0)
    caption = message.caption or ""

    try:
        meta = extract_movie_metadata(file_name, caption)
    except Exception:
        meta = {}

    title = _safe(
        meta.get("title")
        or (file_name.rsplit(".", 1)[0] if file_name else "")
        or (caption[:80] if caption else "")
        or "Unknown"
    )
    year = meta.get("year")
    language = meta.get("language") or "Hindi"
    q_key = _quality_key(meta)

    if not title or title.lower() in ("unknown", "untitled", ""):
        logger.info("auto_index skip empty title msg=%s fname=%s", message.id, file_name)
        await _notify(bot, f"⚠️ Auto-index: title nahi mila\nmsg={message.id}\nfile={file_name or caption[:60]}")
        return

    file_id, db_msg_id = await _copy_to_db(bot, message, caption_text=caption or file_name)
    if not file_id:
        file_id = getattr(media, "file_id", None)
    if not file_id:
        _stats["errors"] += 1
        logger.warning("auto_index no file_id msg=%s", message.id)
        await _notify(bot, f"❌ Auto-index: file_id nahi mila msg={message.id}")
        return

    movie_id = await _get_or_create_movie(title, year, language=language)
    if not movie_id:
        _stats["errors"] += 1
        logger.error("auto_index could not create movie title=%s", title)
        await _notify(bot, f"❌ Auto-index: movie create fail\n{title}")
        return

    quality_data = {
        "file_id": file_id,
        "file_name": _safe(file_name),
        "file_size": file_size,
        "file_size_formatted": format_size(file_size) if file_size else "?",
        "label": q_key,
        "quality": meta.get("quality") or "HD",
        "season": meta.get("season") or None,
        "episode": meta.get("episode") or None,
        "db_msg_id": db_msg_id,
        "source_chat_id": chat_id,
        "source_msg_id": int(message.id),
    }
    try:
        await db.add_quality_to_movie(movie_id, q_key, quality_data)
    except Exception as e:
        _stats["errors"] += 1
        logger.error("add_quality: %s", e)
        await _notify(bot, f"❌ Auto-index add_quality fail: {e}")
        return

    _stats["saved"] += 1

    # Thumb as poster if none
    try:
        movie = await db.get_movie(movie_id)
        if movie and not movie.get("poster_file_id"):
            thumb = getattr(media, "thumbs", None)
            if thumb:
                t = thumb[-1] if isinstance(thumb, list) else thumb
                tid = getattr(t, "file_id", None)
                if tid and hasattr(db, "update_movie_poster"):
                    try:
                        await db.update_movie_poster(movie_id, tid, "channel_thumb")
                    except Exception:
                        pass
                elif tid and getattr(db, "movies_col", None) is not None:
                    await db.movies_col.update_one(
                        {"movie_id": movie_id},
                        {"$set": {"poster_file_id": tid}},
                    )
    except Exception:
        pass

    mkey = _movie_key(title, year)
    _schedule_finalize(bot, mkey, movie_id)
    logger.info(
        "auto_index SAVED title=%s year=%s quality=%s movie_id=%s msg=%s",
        title, year, q_key, movie_id, message.id,
    )
    await _notify(
        bot,
        f"📥 Auto-index SAVED\n"
        f"🎬 {title}" + (f" ({year})" if year else "") + "\n"
        f"📦 {q_key} • {format_size(file_size) if file_size else '?'}\n"
        f"⏳ Group post ~{SETTLE_SECONDS}s baad (aur qualities wait)",
    )


def register_channel_auto_index(bot: Client):
    """Register watcher for AUTO INDEX channel."""
    if not WATCH_CHANNEL_ID:
        logger.warning("Channel auto-index: no WATCH_CHANNEL_ID configured")
        return

    # Broad filter: any message in that chat; media check inside handler
    # group=-2 so it runs early and is not blocked by other handlers
    @bot.on_message(filters.chat(WATCH_CHANNEL_ID), group=-2)
    async def _on_watch_any(client: Client, message: Message):
        try:
            await _handle_channel_media(client, message)
        except Exception as e:
            _stats["errors"] += 1
            logger.exception("channel auto-index handler: %s", e)
            try:
                await _notify(client, f"❌ Auto-index handler error:\n{type(e).__name__}: {e}")
            except Exception:
                pass

    # Also explicit media filter as backup (some pyrofork builds differ)
    @bot.on_message(
        filters.chat(WATCH_CHANNEL_ID)
        & (filters.document | filters.video | filters.audio | filters.animation),
        group=-3,
    )
    async def _on_watch_media(client: Client, message: Message):
        # Dedup: if -2 already processed same message, _stats still fine;
        # add_quality overwrites same quality key — safe.
        try:
            await _handle_channel_media(client, message)
        except Exception as e:
            logger.exception("channel auto-index media handler: %s", e)

    @bot.on_message(filters.command(["autoindex", "auto_index"]) & filters.private)
    async def _autoindex_status(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if uid not in getattr(Config, "ADMINS", set()) and uid != getattr(Config, "BOT_OWNER", 0):
            return
        pending = len([t for t in _pending_finalize.values() if t and not t.done()])
        await message.reply_text(
            f"📡 <b>Channel Auto-Index</b>\n\n"
            f"Watch channel: <code>{WATCH_CHANNEL_ID}</code>\n"
            f"Settle: {SETTLE_SECONDS}s\n"
            f"Seen: {_stats['seen']} | Saved: {_stats['saved']} | "
            f"Posted: {_stats['posted']} | Errors: {_stats['errors']}\n"
            f"Pending finalize: {pending}\n\n"
            f"Is channel me naya video/document daalo — bot auto save + group post karega.",
            quote=True,
        )

    logger.info(
        "Channel auto-index ON — watching %s (settle=%ss)",
        WATCH_CHANNEL_ID, SETTLE_SECONDS,
    )
