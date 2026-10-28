import asyncio
import logging
import re
from typing import Optional

from pyrogram import Client
from pyrogram.errors import FloodWait
from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size
from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons

logger = logging.getLogger("IndexingWorker")
_CANCEL = {}
_RUNNING = {}

def request_cancel(admin_id: int):
    _CANCEL[int(admin_id)] = True

def is_running(admin_id: int) -> bool:
    return bool(_RUNNING.get(int(admin_id)))

def _norm_quality(q: str) -> str:
    if not q: return "720p"
    s = str(q).lower()
    if "1080" in s or "fhd" in s: return "1080p"
    if "720" in s or "hd" in s: return "720p"
    if "480" in s or "sd" in s or "360" in s: return "480p"
    return "720p"

async def _status(bot, chat_id, msg_id, text, show_cancel=True):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="idx_cancel")]]) if show_cancel else None
    try:
        await bot.edit_message_text(chat_id, msg_id, text, reply_markup=kb)
    except Exception:
        pass

async def _bot_is_admin(client, channel_id):
    try:
        m = await client.get_chat_member(channel_id, "me")
        st = str(getattr(m.status, "value", m.status) or m.status).lower()
        return any(x in st for x in ("administrator", "owner", "creator"))
    except Exception:
        return False

async def _copy_to_db(bot, message):
    db_ch = getattr(Config, "DB_CHANNEL", None)
    media = message.document or message.video or message.audio or message.animation
    if not db_ch:
        return getattr(media, "file_id", None) if media else None
    try:
        copied = await message.copy(int(db_ch))
        m = copied.document or copied.video or copied.audio or copied.animation
        return getattr(m, "file_id", None) if m else None
    except Exception:
        return getattr(media, "file_id", None) if media else None

async def run_indexing(bot, admin_id, channel_id, start_id, end_id, status_chat_id, status_msg_id=None):
    admin_id = int(admin_id)
    channel_id = int(channel_id)
    start_id, end_id = int(start_id), int(end_id)
    if end_id < start_id:
        start_id, end_id = end_id, start_id
    _RUNNING[admin_id] = True
    _CANCEL[admin_id] = False
    total = end_id - start_id + 1
    scanned = saved = posted = 0
    bucket = {}
    try:
        for msg_id in range(start_id, end_id + 1):
            if _CANCEL.get(admin_id):
                break
            scanned += 1
            if status_msg_id and (scanned % 3 == 0 or scanned == 1):
                await _status(bot, status_chat_id, status_msg_id,
                    f"Indexing {scanned}/{total}\nSaved: {saved}\nPosted: {posted}")
            try:
                msg = await bot.get_messages(channel_id, msg_id)
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                continue
            except Exception:
                continue
            if not msg or getattr(msg, "empty", False):
                continue
            media = msg.document or msg.video or msg.audio or msg.animation
            if not media:
                continue
            file_name = getattr(media, "file_name", None) or ""
            caption = msg.caption or ""
            meta = extract_movie_metadata(file_name, caption)
            title = (meta.get("title") or file_name or "Unknown").strip()
            year = meta.get("year")
            fq = _norm_quality(meta.get("quality_label") or meta.get("quality") or "HD")
            file_id = await _copy_to_db(bot, msg)
            if not file_id:
                continue
            try:
                mid = await db.add_quality_to_movie(title, year, fq, file_id, file_name, getattr(media, "file_size", 0) or 0)
                if mid:
                    saved += 1
                    bucket[(title.lower(), year)] = mid
            except Exception as e:
                logger.warning("save: %s", e)
        for mid in set(bucket.values()):
            try:
                movie = await db.get_movie(mid)
                if not movie:
                    continue
                text = build_final_movie_card_text(movie)
                buttons = build_final_movie_card_buttons(movie, for_group=True)
                target = getattr(Config, "MOVIE_GROUP_ID", None)
                if target:
                    await bot.send_message(target, text, reply_markup=buttons, disable_web_page_preview=True)
                    posted += 1
            except Exception as e:
                logger.warning("post: %s", e)
        if status_msg_id:
            await _status(bot, status_chat_id, status_msg_id,
                f"Done\nScanned: {scanned}/{total}\nSaved: {saved}\nPosted: {posted}", show_cancel=False)
    except Exception as e:
        logger.error("indexing: %s", e)
        if status_msg_id:
            await _status(bot, status_chat_id, status_msg_id, f"Error: {e}", show_cancel=False)
    finally:
        _RUNNING[admin_id] = False
        _CANCEL[admin_id] = False
