# handlers/indexing/worker_full_a.py — scan, save, post
import asyncio, logging, re
from typing import Optional
from pyrogram import Client
from pyrogram.errors import FloodWait
from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size
from handlers.movie_card import build_final_movie_card_text

logger = logging.getLogger("IndexingWorker")
_CANCEL, _RUNNING = {}, {}
_LAST_POST_ERR = None  # last group-post failure reason
_CANCEL_HANDLER_ADDED = False

def request_cancel(admin_id: int):
    _CANCEL[int(admin_id)] = True

def is_running(admin_id: int) -> bool:
    return bool(_RUNNING.get(int(admin_id)))

def _ensure_cancel_handler(bot: Client):
    global _CANCEL_HANDLER_ADDED
    if _CANCEL_HANDLER_ADDED:
        return
    try:
        from pyrogram import filters as _f
        from pyrogram.handlers import CallbackQueryHandler
        async def _on_idx_cancel(client, query):
            request_cancel(int(query.from_user.id) if query.from_user else 0)
            try:
                await query.answer("Cancelling...", show_alert=True)
            except Exception:
                pass
        bot.add_handler(CallbackQueryHandler(_on_idx_cancel, _f.regex(r"^idx_cancel$")), group=-11)
        _CANCEL_HANDLER_ADDED = True
    except Exception as e:
        logger.warning("cancel handler: %s", e)

def _norm_quality(q: str) -> str:
    s = str(q or "").lower()
    if "1080" in s or "fhd" in s: return "1080p"
    if "720" in s or "hd" in s: return "720p"
    if "480" in s or "sd" in s or "360" in s: return "480p"
    return "720p"

_ALLOWED_Q = ("480p", "720p", "1080p")

async def _prune_qualities(movie_id: str, keep_key: str = None):
    try:
        mov = await db.get_movie(movie_id)
        if not mov: return
        quals = dict(mov.get("qualities") or {})
        cleaned = {}
        for k, v in quals.items():
            nk = _norm_quality(str(k))
            if nk in _ALLOWED_Q:
                cleaned[nk] = v
        if keep_key and keep_key in _ALLOWED_Q and keep_key in quals:
            cleaned[keep_key] = quals[keep_key]
        if cleaned != quals and getattr(db, "movies_col", None) is not None:
            await db.movies_col.update_one({"movie_id": movie_id}, {"$set": {"qualities": cleaned}})
        if getattr(db, "_mem_movies", None) is not None and movie_id in db._mem_movies:
            db._mem_movies[movie_id]["qualities"] = cleaned
    except Exception as e:
        logger.warning("prune: %s", e)

def _safe(text, fallback=""):
    try:
        s = str(text if text is not None else fallback)
        return s.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")
    except Exception:
        return fallback

async def _status(bot, chat_id, msg_id, text, show_cancel=True):
    try:
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="idx_cancel")]]) if show_cancel else None
        await bot.edit_message_text(chat_id, msg_id, _safe(text), reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass

async def _bot_is_admin(client, channel_id):
    try:
        me = await client.get_me()
        m = await client.get_chat_member(int(channel_id), me.id)
        return str(getattr(m, "status", "")).lower() in ("administrator", "creator", "owner")
    except Exception:
        return False

async def _copy_to_db(bot, message, caption_text=None):
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
        sent = await message.copy(int(db_ch), caption=cap if cap else None)
        return (getattr(sent.document or sent.video or sent.audio or sent.animation, "file_id", None) or orig_fid), (sent.id if sent else None)
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            sent = await message.copy(int(db_ch), caption=cap if cap else None)
            return (getattr(sent.document or sent.video or sent.audio or sent.animation, "file_id", None) or orig_fid), (sent.id if sent else None)
        except Exception:
            return orig_fid, None
    except Exception as e:
        logger.warning("copy_to_db: %s", e)
        return orig_fid, None

async def _stabilize_poster(bot, poster_file_id: str) -> Optional[str]:
    if not poster_file_id: return None
    db_ch = getattr(Config, "DB_CHANNEL", None)
    if not db_ch: return poster_file_id
    try:
        from io import BytesIO
        data = await bot.download_media(poster_file_id, in_memory=True)
        if data is None:
            raise RuntimeError("download empty")
        if hasattr(data, "getvalue"):
            raw = data.getvalue()
        else:
            raw = data.read() if hasattr(data, "read") else bytes(data)
        if raw:
            bio = BytesIO(raw); bio.name = "poster.jpg"
            sent = await bot.send_photo(int(db_ch), photo=bio)
            return sent.photo.file_id if sent and sent.photo else poster_file_id
    except Exception as e:
        logger.warning("stabilize download: %s", e)
    try:
        sent = await bot.send_photo(int(db_ch), photo=poster_file_id)
        return sent.photo.file_id if sent and sent.photo else poster_file_id
    except Exception:
        return poster_file_id

def _extract_poster_from_media(message):
    try:
        if message.photo:
            return message.photo.file_id
        for attr in ("video", "document", "animation"):
            m = getattr(message, attr, None)
            if m and getattr(m, "thumbs", None):
                thumbs = m.thumbs
                if thumbs:
                    return thumbs[-1].file_id
    except Exception:
        pass
    return None

async def _extract_poster_id(bot, message) -> Optional[str]:
    try:
        return _extract_poster_from_media(message)
    except Exception:
        return None

def _clean_title(raw: str) -> str:
    if not raw: return ""
    base = str(raw)
    base = re.sub(r"(?i)\b(480p|720p|1080p|2160p|4k|hdrip|web[- ]?dl|bluray|brrip|hdcam|camrip|dvdrip|x264|x265|hevc|aac|dts|dd5\.?1|10bit|8bit|multi\s*audio)\b", " ", base)
    base = re.sub(r"(?i)\b(mkv|mp4|avi|m4v|mov)\b", " ", base)
    base = re.sub(r"(?i)^\s*(?:@?[\w]*toonworld[\w]*|@?movies?[_\d]*|@?[\w]*movies?[_\d]*)\b\s*", " ", base)
    base = re.sub(r"^\s*[A-Z0-9_]{10,}\s+", " ", base)
    base = re.sub(r"[\[\]\(\)\{\}|_\-]+", " ", base)
    return re.sub(r"\s+", " ", base).strip()

def _norm_key(t: str) -> str:
    t = (t or "").lower()
    t = re.sub(r"[^\w\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()

def _title_from_name(file_name: str, caption: str = ""):
    meta = {}
    try:
        meta = extract_movie_metadata(file_name or "", caption or "") or {}
    except Exception:
        meta = {}
    title = meta.get("title") or ""
    year = meta.get("year")
    q = meta.get("quality") or ""
    if not title:
        raw = (caption or file_name or "").strip()
        title = _clean_title(raw.split("\n")[0] if raw else "")
        if not year:
            m = re.search(r"(19|20)\d{2}", raw)
            if m: year = m.group(0)
    title = _clean_title(title) or title
    if not title:
        title = re.sub(r"\.[a-z0-9]{2,4}$", "", file_name or "", flags=re.I).strip() or "Unknown"
    return title, year, _norm_quality(q)

def _build_quality_rows(movie: dict, movie_id: str):
    from pyrogram.types import InlineKeyboardButton
    qualities = movie.get("qualities") or {}
    order = {"480p": 0, "720p": 1, "1080p": 2}
    sorted_q = sorted(qualities.items(), key=lambda item: order.get(str(item[0]), 99))
    rows = []
    bot_user = (getattr(Config, "BOT_USERNAME", None) or "").lstrip("@") or "Ash_best_movie_bot"
    for q_name, q_data in sorted_q:
        size_str = q_data.get("file_size_formatted") or ""
        if not size_str and q_data.get("file_size"):
            try: size_str = format_size(int(q_data["file_size"]))
            except Exception: size_str = ""
        label = f"⬇️ Download {q_name}" + (f" ({size_str})" if size_str else "")
        channel_msg_id = q_data.get("channel_msg_id")
        q_slug = str(q_name).replace(" ", "_").lower()
        if channel_msg_id:
            url = f"https://t.me/{bot_user}?start=f_{channel_msg_id}"
        else:
            url = f"https://t.me/{bot_user}?start=q_{movie_id}_{q_slug}"
        rows.append([InlineKeyboardButton(label, url=url)])
    rows.append([InlineKeyboardButton("📢 Updates Channel", url="https://t.me/ash_movie_1782")])
    return rows

async def _post_movie_to_group(bot: Client, movie_id: str, force_new: bool = False):
    """Group me movie card post / edit. Fail pe reason logger me + last_error set."""
    from pyrogram.types import InlineKeyboardMarkup
    global _LAST_POST_ERR
    movie = await db.get_movie(movie_id)
    if not movie:
        _LAST_POST_ERR = "movie not in db"
        return None
    target = getattr(Config, "MOVIE_GROUP_ID", None)
    if not target:
        _LAST_POST_ERR = "MOVIE_GROUP_ID missing"
        return None
    try:
        card_text = build_final_movie_card_text(movie)
        rows = _build_quality_rows(movie, movie_id)
        buttons = InlineKeyboardMarkup(rows) if rows else None
        poster_id = movie.get("poster_file_id")
        g_chat, g_msg = movie.get("group_chat_id"), movie.get("group_msg_id")
        if not force_new and g_chat and g_msg:
            try:
                if poster_id:
                    try:
                        await bot.edit_message_caption(int(g_chat), int(g_msg), caption=_safe(card_text), reply_markup=buttons)
                        return "edit"
                    except Exception:
                        pass
                await bot.edit_message_text(int(g_chat), int(g_msg), text=_safe(card_text), reply_markup=buttons, disable_web_page_preview=True)
                return "edit"
            except Exception:
                pass  # fall through → new post

        sent = None
        # 1) poster try
        if poster_id:
            try:
                sent = await bot.send_photo(int(target), photo=poster_id, caption=_safe(card_text), reply_markup=buttons)
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                try:
                    sent = await bot.send_photo(int(target), photo=poster_id, caption=_safe(card_text), reply_markup=buttons)
                except Exception as e2:
                    logger.warning("post photo flood-retry fail %s: %s", movie_id, e2)
            except Exception as e:
                logger.warning("post photo fail %s: %s", movie_id, e)
                try:
                    stable = await _stabilize_poster(bot, poster_id)
                    if stable:
                        sent = await bot.send_photo(int(target), photo=stable, caption=_safe(card_text), reply_markup=buttons)
                        if getattr(db, "movies_col", None) is not None:
                            await db.movies_col.update_one({"movie_id": movie_id}, {"$set": {"poster_file_id": stable}})
                except Exception as e3:
                    logger.warning("post stabilize fail %s: %s", movie_id, e3)
                    sent = None

        # 2) text fallback (always if photo failed)
        if not sent:
            try:
                sent = await bot.send_message(
                    int(target), text=_safe(card_text), reply_markup=buttons, disable_web_page_preview=True
                )
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                try:
                    sent = await bot.send_message(
                        int(target), text=_safe(card_text), reply_markup=buttons, disable_web_page_preview=True
                    )
                except Exception as e2:
                    _LAST_POST_ERR = f"text flood-retry: {type(e2).__name__}: {e2}"
                    logger.warning("group text send fail %s: %s", movie_id, e2)
                    return None
            except Exception as e:
                _LAST_POST_ERR = f"text: {type(e).__name__}: {e}"
                logger.warning("group text send fail %s: %s", movie_id, e)
                return None

        if sent:
            try:
                if hasattr(db, "update_movie_group_post"):
                    await db.update_movie_group_post(movie_id, sent.chat.id, sent.id)
            except Exception as e:
                logger.warning("update_movie_group_post: %s", e)
            _LAST_POST_ERR = None
            return "new"
        _LAST_POST_ERR = "sent is None"
        return None
    except Exception as e:
        _LAST_POST_ERR = f"{type(e).__name__}: {e}"
        logger.warning("post_movie %s: %s", movie_id, e)
    return None

async def run_indexing(bot, admin_id, channel_id, start_id, end_id, status_chat_id, status_msg_id=None):
    admin_id, channel_id = int(admin_id), int(channel_id)
    start_id, end_id = int(start_id), int(end_id)
    if end_id < start_id:
        start_id, end_id = end_id, start_id
    _RUNNING[admin_id] = True
    _CANCEL[admin_id] = False
    _ensure_cancel_handler(bot)
    total = end_id - start_id + 1
    scanned = saved = posted = media_found = 0
    bucket, posted_set = {}, set()
    current_key = None
    last_title = ""
    pending_poster = None
    try:
        if status_msg_id:
            await _status(bot, status_chat_id, status_msg_id,
                f"Indexing...\nChannel <code>{channel_id}</code>\n"
                f"Range <code>{start_id}</code> → <code>{end_id}</code>\n"
                f"Scanned: 0/{total}\nSaved: 0\nPosted: 0")
        BATCH = 40
        msg_id = start_id
        while msg_id <= end_id:
            if _CANCEL.get(admin_id):
                break
            batch_ids = list(range(msg_id, min(msg_id + BATCH, end_id + 1)))
            try:
                raw = await bot.get_messages(channel_id, batch_ids)
                if not isinstance(raw, list):
                    raw = [raw] if raw else []
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
                try:
                    raw = await bot.get_messages(channel_id, batch_ids)
                    if not isinstance(raw, list):
                        raw = [raw] if raw else []
                except Exception:
                    raw = []
            except Exception as e:
                logger.warning("get_messages: %s", e)
                raw = []
            for msg in raw:
                scanned += 1
                if msg is None or getattr(msg, "empty", False):
                    continue
                try:
                    mid = int(msg.id)
                except Exception:
                    continue
                if status_msg_id and (scanned % 25 == 0 or media_found <= 1):
                    extra = f"\nLast: {last_title[:40]}" if last_title else ""
                    await _status(bot, status_chat_id, status_msg_id,
                        f"Indexing {scanned}/{total}\nMedia: {media_found}\n"
                        f"Saved: {saved}\nPosted: {posted}{extra}")
                if msg.photo and not (msg.document or msg.video or msg.audio or msg.animation):
                    try:
                        pending_poster = msg.photo.file_id
                    except Exception:
                        pass
                    continue
                media = msg.document or msg.video or msg.audio or msg.animation
                if not media:
                    continue
                media_found += 1
                file_name = getattr(media, "file_name", None) or (msg.caption or "")[:80] or f"file_{mid}"
                caption = msg.caption or ""
                filesize = int(getattr(media, "file_size", 0) or 0)
                title, year, final_quality = _title_from_name(file_name, caption)
                if not title:
                    continue
                quality_key = final_quality
                norm = _norm_key(title)
                key = (norm, year)
                if current_key is not None and key != current_key:
                    prev = bucket.get(current_key)
                    if prev and prev not in posted_set:
                        result = await _post_movie_to_group(bot, prev, force_new=True)
                        if result == "new":
                            posted += 1
                            posted_set.add(prev)
                        elif result:
                            posted_set.add(prev)
                    current_key = key
                elif current_key is None:
                    current_key = key
                movie_id = bucket.get(key)
                if not movie_id:
                    existing = None
                    try:
                        existing = await db.find_movie_by_title_year(title, year)
                    except Exception:
                        pass
                    if existing and existing.get("movie_id"):
                        movie_id = existing["movie_id"]
                    else:
                        poster_file_id = await _extract_poster_id(bot, msg) or pending_poster
                        pending_poster = None
                        try:
                            doc = await db.create_movie(title=title, normalized_title=norm, year=year, poster_file_id=poster_file_id)
                            movie_id = (doc or {}).get("movie_id") or (doc.get("_id") if isinstance(doc, dict) else None)
                        except Exception as e:
                            logger.warning("create_movie: %s", e)
                            continue
                    if not movie_id:
                        continue
                    bucket[key] = movie_id
                try:
                    mov = await db.get_movie(movie_id)
                    if mov and not mov.get("poster_file_id"):
                        pid = await _extract_poster_id(bot, msg) or pending_poster
                        pending_poster = None
                        if pid and getattr(db, "movies_col", None) is not None:
                            await db.movies_col.update_one({"movie_id": movie_id}, {"$set": {"poster_file_id": pid}})
                except Exception:
                    pass
                cap_txt = f"{title}" + (f" ({year})" if year else "") + f"\n{file_name}\nQuality: {final_quality}"
                file_id, channel_msg_id = await _copy_to_db(bot, msg, caption_text=cap_txt)
                if not file_id:
                    continue
                quality_data = {
                    "file_id": file_id, "file_name": file_name, "file_size": filesize,
                    "file_size_formatted": format_size(filesize) if filesize else "",
                    "label": quality_key, "quality": final_quality,
                    "source_channel_id": channel_id, "source_msg_id": mid,
                    "channel_msg_id": channel_msg_id, "caption": cap_txt,
                }
                try:
                    await db.add_quality_to_movie(movie_id, quality_key, quality_data)
                    await _prune_qualities(movie_id, keep_key=quality_key)
                    saved += 1
                    last_title = title
                except Exception as e:
                    logger.warning("add_quality: %s", e)
                    continue
                try:
                    if movie_id not in posted_set:
                        result = await _post_movie_to_group(bot, movie_id, force_new=True)
                        if result == "new":
                            posted += 1
                            posted_set.add(movie_id)
                        elif result:
                            posted_set.add(movie_id)
                    else:
                        await _post_movie_to_group(bot, movie_id, force_new=False)
                except Exception as e:
                    logger.warning("post: %s", e)
                await asyncio.sleep(0.02)
            msg_id += BATCH
        for mid in list(bucket.values()):
            if mid not in posted_set:
                result = await _post_movie_to_group(bot, mid, force_new=True)
                if result == "new":
                    posted += 1
                    posted_set.add(mid)
        if status_msg_id:
            tag = "Cancelled" if _CANCEL.get(admin_id) else "Done"
            err = f"\n⚠️ Post err: {_LAST_POST_ERR}" if (posted == 0 and _LAST_POST_ERR) else ""
            await _status(bot, status_chat_id, status_msg_id,
                f"{tag}\nScanned: {scanned}/{total}\nMedia: {media_found}\nSaved: {saved}\nPosted: {posted}{err}",
                show_cancel=False)
    except Exception as e:
        logger.exception("run_indexing: %s", e)
        if status_msg_id:
            await _status(bot, status_chat_id, status_msg_id, f"Error: {type(e).__name__}: {e}", show_cancel=False)
    finally:
        _RUNNING[admin_id] = False
        _CANCEL[admin_id] = False
