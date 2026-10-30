# handlers/indexing/worker_full_a.py — scan, save, post (correct DB API)
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
_CANCEL_HANDLER_ADDED = False


def request_cancel(admin_id: int):
    _CANCEL[int(admin_id)] = True


def is_running(admin_id: int) -> bool:
    return bool(_RUNNING.get(int(admin_id)))


def _ensure_cancel_handler(bot: Client):
    """Register idx_cancel callback once so Cancel button works during indexing."""
    global _CANCEL_HANDLER_ADDED
    if _CANCEL_HANDLER_ADDED:
        return
    try:
        from pyrogram import filters as _f
        from pyrogram.handlers import CallbackQueryHandler
        from pyrogram.types import CallbackQuery

        async def _on_idx_cancel(client: Client, query: CallbackQuery):
            uid = int(query.from_user.id) if query.from_user else 0
            request_cancel(uid)
            try:
                await query.answer("⏹ Cancelling indexing...", show_alert=True)
            except Exception:
                pass
            try:
                await query.edit_message_text(
                    "⏹ Cancel requested.\nCurrent file ke baad indexing band ho jayegi."
                )
            except Exception:
                pass

        bot.add_handler(
            CallbackQueryHandler(_on_idx_cancel, _f.regex(r"^idx_cancel$")),
            group=-11,
        )
        _CANCEL_HANDLER_ADDED = True
        logger.info("idx_cancel handler registered")
    except Exception as e:
        logger.warning("cancel handler register fail: %s", e)


def _norm_quality(q: str) -> str:
    if not q:
        return "720p"
    s = str(q).lower()
    if "1080" in s or "fhd" in s or "1920" in s:
        return "1080p"
    if "720" in s or "hd" in s or "1280" in s:
        return "720p"
    if "480" in s or "sd" in s or "360" in s or "240" in s:
        return "480p"
    return "720p"

_ALLOWED_Q = ("480p", "720p", "1080p")


async def _prune_qualities(movie_id: str, keep_key: str = None):
    """Only 480p / 720p / 1080p — one slot each. Same res always overwrites."""
    try:
        mov = await db.get_movie(movie_id)
        if not mov:
            return
        quals = dict(mov.get("qualities") or {})
        if not quals:
            return
        cleaned = {}
        for k, v in quals.items():
            nk = _norm_quality(str(k))
            if nk not in _ALLOWED_Q:
                continue
            cleaned[nk] = v
        if keep_key and keep_key in _ALLOWED_Q and keep_key in quals:
            cleaned[keep_key] = quals[keep_key]
        if cleaned == quals:
            return
        if getattr(db, "movies_col", None) is not None:
            await db.movies_col.update_one(
                {"movie_id": movie_id},
                {"$set": {"qualities": cleaned}},
            )
        if getattr(db, "_mem_movies", None) is not None and movie_id in db._mem_movies:
            db._mem_movies[movie_id]["qualities"] = cleaned
    except Exception as e:
        logger.warning("prune_qualities: %s", e)


def _safe(t: str) -> str:
    if not t:
        return ""
    return t[:4000]


async def _status(bot, chat_id, msg_id, text, show_cancel=True):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = None
    if show_cancel:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="idx_cancel")]])
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


async def _copy_to_db(bot, message, caption_text: str = None):
    """Copy media to DB_CHANNEL. Returns (file_id, channel_msg_id)."""
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
        fid = getattr(m, "file_id", None) if m else orig_fid
        return fid, getattr(copied, "id", None)
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            copied = await message.copy(int(db_ch), caption=cap if cap else None)
            m = copied.document or copied.video or copied.audio or copied.animation
            fid = getattr(m, "file_id", None) if m else orig_fid
            return fid, getattr(copied, "id", None)
        except Exception:
            return orig_fid, None
    except Exception as e:
        logger.warning("copy_to_db: %s", e)
        return orig_fid, None


async def _stabilize_poster(bot, poster_file_id: str) -> Optional[str]:
    """Download poster bytes and re-upload to DB_CHANNEL for a stable file_id."""
    if not poster_file_id:
        return None
    db_ch = getattr(Config, "DB_CHANNEL", None)
    if not db_ch:
        return poster_file_id

    try:
        from io import BytesIO
        data = await bot.download_media(poster_file_id, in_memory=True)
        raw = None
        if data is not None:
            if hasattr(data, "getvalue"):
                raw = data.getvalue()
            elif hasattr(data, "read"):
                raw = data.read()
            elif isinstance(data, (bytes, bytearray)):
                raw = bytes(data)
        if raw and len(raw) > 100:
            bio = BytesIO(raw)
            bio.name = "poster.jpg"
            sent = await bot.send_photo(int(db_ch), photo=bio)
            if sent and sent.photo:
                return sent.photo.file_id
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 3)) + 1)
    except Exception as e:
        logger.warning("stabilize download: %s", e)

    try:
        sent = await bot.send_photo(int(db_ch), photo=poster_file_id)
        if sent and sent.photo:
            return sent.photo.file_id
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 3)) + 1)
        try:
            sent = await bot.send_photo(int(db_ch), photo=poster_file_id)
            if sent and sent.photo:
                return sent.photo.file_id
        except Exception as e2:
            logger.warning("stabilize file_id retry: %s", e2)
    except Exception as e:
        logger.warning("stabilize file_id: %s", e)
    return None


def _best_thumb_id(thumbs) -> Optional[str]:
    if not thumbs:
        return None
    try:
        best = max(thumbs, key=lambda t: int(getattr(t, "file_size", 0) or 0))
        return best.file_id
    except Exception:
        try:
            return thumbs[-1].file_id
        except Exception:
            return None


async def _extract_poster_id(bot, message) -> Optional[str]:
    """Get poster file_id and stabilize via download→DB_CHANNEL re-upload."""
    raw = None
    try:
        if message.photo:
            db_ch = getattr(Config, "DB_CHANNEL", None)
            if db_ch:
                try:
                    copied = await message.copy(int(db_ch))
                    if copied and copied.photo:
                        return copied.photo.file_id
                except Exception as e:
                    logger.warning("poster copy: %s", e)
            raw = message.photo.file_id
        elif message.video and getattr(message.video, "thumbs", None):
            raw = _best_thumb_id(message.video.thumbs)
        elif message.document and getattr(message.document, "thumbs", None):
            raw = _best_thumb_id(message.document.thumbs)
        elif message.animation and getattr(message.animation, "thumbs", None):
            raw = _best_thumb_id(message.animation.thumbs)
    except Exception:
        pass
    if raw:
        return await _stabilize_poster(bot, raw)
    return None


_NOISE_RE = re.compile(
    r"\b("
    r"HDTC|HDCAM|HDTS|HDRip|HDRIP|WEBRip|WEB-DL|WEBDL|BluRay|BRRip|BDRip|"
    r"x264|x265|HEVC|AAC|HC|ESub|CineVood|"
    r"Hindi|Punjabi|Tamil|Telugu|Malayalam|Marathi|Kannada|English|Eng|Hind|"
    r"Org|Original|Dual|Audio|Multi|HQ|Proper|REPACK|Extended|Uncut|"
    r"\d{3,4}p|10bit|8bit"
    r")\b",
    re.I,
)


def _clean_title(raw: str) -> str:
    """Strip quality/lang/source noise so same movie groups together."""
    if not raw:
        return ""
    base = re.sub(r"\.[a-z0-9]{2,4}$", "", str(raw), flags=re.I)
    base = re.sub(r"\bDUAL\s*A(?:UDIO)?\b", " ", base, flags=re.I)
    base = re.sub(r"\bMULTI\s*AUDIO\b", " ", base, flags=re.I)
    base = _NOISE_RE.sub(" ", base)
    base = re.sub(r"[&]+", " and ", base)
    base = re.sub(r"[_\.\-\[\]\(\)\{\},:;/+]+", " ", base)
    base = re.sub(r"\b(19\d{2}|20\d{2})\b", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    base = re.sub(r"\b[A-Za-z]\b", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def _norm_key(title: str) -> str:
    t = _clean_title(title).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_from_name(file_name: str, caption: str = "") -> tuple:
    meta = {}
    try:
        meta = extract_movie_metadata(file_name or "", caption or "") or {}
    except Exception as e:
        logger.warning("extract_meta: %s", e)
    title = (meta.get("title") or "").strip()
    year = meta.get("year")
    q = meta.get("quality_label") or meta.get("quality") or ""

    combined = f"{file_name or ''} {caption or ''}".strip()
    if not year:
        ym = re.search(r"\b(19\d{2}|20\d{2})\b", combined)
        if ym:
            try:
                year = int(ym.group(1))
            except Exception:
                year = None
    if not q:
        q = _norm_quality(combined)

    if title:
        title = _clean_title(title)
    if not title:
        title = _clean_title(file_name or "") or _clean_title(caption or "")
    if not title:
        title = (file_name or "Unknown")[:60]

    return title, year, _norm_quality(q)


def _build_quality_rows(movie: dict, movie_id: str):
    from pyrogram.types import InlineKeyboardButton
    qualities = movie.get("qualities") or {}
    order = {"480p": 0, "720p": 1, "1080p": 2}
    sorted_q = sorted(
        qualities.items(),
        key=lambda item: order.get(str(item[0]), 99),
    )
    rows = []
    bot_user = (getattr(Config, "BOT_USERNAME", None) or "").lstrip("@") or "Ash_best_movie_bot"
    for q_name, q_data in sorted_q:
        size_str = q_data.get("file_size_formatted") or ""
        if not size_str and q_data.get("file_size"):
            try:
                size_str = format_size(int(q_data["file_size"]))
            except Exception:
                size_str = ""
        label = f"⬇️ Download {q_name}" + (f" ({size_str})" if size_str else "")
        channel_msg_id = q_data.get("channel_msg_id")
        q_slug = str(q_name).replace(" ", "_").lower()
        if channel_msg_id:
            url = f"https://t.me/{bot_user}?start=f_{channel_msg_id}"
        else:
            url = f"https://t.me/{bot_user}?start=q_{movie_id}_{q_slug}"
        rows.append([InlineKeyboardButton(label, url=url)])
    rows.append([
        InlineKeyboardButton("📢 Updates Channel", url="https://t.me/ash_movie_1782")
    ])
    return rows


async def _post_movie_to_group(bot: Client, movie_id: str, force_new: bool = False):
    """Post or update group card. Returns 'new' | 'edit' | None."""
    from pyrogram.types import InlineKeyboardMarkup
    movie = await db.get_movie(movie_id)
    if not movie:
        return None
    target = getattr(Config, "MOVIE_GROUP_ID", None)
    if not target:
        logger.warning("MOVIE_GROUP_ID not set")
        return None
    try:
        card_text = build_final_movie_card_text(movie)
        rows = _build_quality_rows(movie, movie_id)
        buttons = InlineKeyboardMarkup(rows) if rows else None
        poster_id = movie.get("poster_file_id")

        g_chat = movie.get("group_chat_id")
        g_msg = movie.get("group_msg_id")
        if not force_new and g_chat and g_msg:
            try:
                if poster_id:
                    try:
                        await bot.edit_message_caption(
                            chat_id=int(g_chat),
                            message_id=int(g_msg),
                            caption=_safe(card_text),
                            reply_markup=buttons,
                        )
                        return "edit"
                    except Exception:
                        pass
                await bot.edit_message_text(
                    chat_id=int(g_chat),
                    message_id=int(g_msg),
                    text=_safe(card_text),
                    reply_markup=buttons,
                    disable_web_page_preview=True,
                )
                return "edit"
            except Exception as e:
                logger.warning("edit group post failed (will send new): %s", e)

        sent = None
        try:
            if poster_id:
                try:
                    sent = await bot.send_photo(
                        chat_id=int(target),
                        photo=poster_id,
                        caption=_safe(card_text),
                        reply_markup=buttons,
                    )
                except Exception as e:
                    logger.warning("send_photo (retry stabilize): %s", e)
                    try:
                        stable = await _stabilize_poster(bot, poster_id)
                        if stable:
                            sent = await bot.send_photo(
                                chat_id=int(target),
                                photo=stable,
                                caption=_safe(card_text),
                                reply_markup=buttons,
                            )
                            try:
                                if getattr(db, "movies_col", None) is not None:
                                    await db.movies_col.update_one(
                                        {"movie_id": movie_id}, {"$set": {"poster_file_id": stable}}
                                    )
                            except Exception:
                                pass
                    except Exception as e2:
                        logger.warning("send_photo final fail: %s", e2)
                        sent = None
            if not sent:
                sent = await bot.send_message(
                    chat_id=int(target),
                    text=_safe(card_text),
                    reply_markup=buttons,
                    disable_web_page_preview=True,
                )
        except FloodWait as e:
            wait = int(getattr(e, "value", 5)) + 1
            logger.warning("FloodWait %ss on group post", wait)
            await asyncio.sleep(wait)
            try:
                sent = await bot.send_message(
                    chat_id=int(target),
                    text=_safe(card_text),
                    reply_markup=buttons,
                    disable_web_page_preview=True,
                )
            except Exception as e2:
                logger.warning("group post retry fail: %s", e2)
                return None
        except Exception as e:
            logger.warning("group send fail: %s", e)
            return None

        if sent:
            try:
                if hasattr(db, "update_movie_group_post"):
                    await db.update_movie_group_post(movie_id, sent.chat.id, sent.id)
            except Exception:
                pass
            return "new"
    except Exception as e:
        logger.warning("post_movie: %s", e)
    return None


async def run_indexing(
    bot: Client,
    admin_id: int,
    channel_id: int,
    start_id: int,
    end_id: int,
    status_chat_id: int,
    status_msg_id=None,
):
    admin_id = int(admin_id)
    channel_id = int(channel_id)
    start_id, end_id = int(start_id), int(end_id)
    if end_id < start_id:
        start_id, end_id = end_id, start_id

    _RUNNING[admin_id] = True
    _CANCEL[admin_id] = False
    _ensure_cancel_handler(bot)

    total = end_id - start_id + 1
    scanned = saved = posted = 0
    bucket = {}
    posted_set = set()
    current_key = None

    try:
        if status_msg_id:
            await _status(
                bot, status_chat_id, status_msg_id,
                f"📥 Indexing...\nChannel <code>{channel_id}</code>\n"
                f"Range <code>{start_id}</code> → <code>{end_id}</code>\n"
                f"Scanned: 0/{total}\nSaved: 0\nPosted: 0",
            )

        try:
            ok = await _bot_is_admin(bot, channel_id)
            if not ok:
                logger.warning("bot may not be admin in %s", channel_id)
        except Exception:
            pass

        BATCH = 40
        last_title = ""
        pending_poster = None
        msg_id = start_id
        while msg_id <= end_id:
            if _CANCEL.get(admin_id):
                break

            batch_ids = list(range(msg_id, min(msg_id + BATCH, end_id + 1)))
            msgs = []
            try:
                raw = await bot.get_messages(channel_id, batch_ids)
                if isinstance(raw, list):
                    msgs = raw
                elif raw is not None:
                    msgs = [raw]
            except FloodWait as e:
                wait = int(getattr(e, "value", 5)) + 1
                logger.warning("FloodWait %ss on get_messages", wait)
                await asyncio.sleep(wait)
                try:
                    raw = await bot.get_messages(channel_id, batch_ids)
                    if isinstance(raw, list):
                        msgs = raw
                    elif raw is not None:
                        msgs = [raw]
                except Exception as e2:
                    logger.warning("get_messages retry fail: %s", e2)
                    msgs = []
            except Exception as e:
                logger.warning("get_messages batch: %s", e)
                msgs = []

            by_id = {}
            for m in msgs:
                if m is None:
                    continue
                try:
                    by_id[int(m.id)] = m
                except Exception:
                    pass

            for mid in batch_ids:
                if _CANCEL.get(admin_id):
                    break
                scanned += 1
                if status_msg_id and (scanned % 5 == 0 or scanned == total):
                    extra = f"\nLast: {last_title[:40]}" if last_title else ""
                    await _status(
                        bot, status_chat_id, status_msg_id,
                        f"Indexing {scanned}/{total}\nSaved: {saved}\nPosted: {posted}{extra}",
                    )

                msg = by_id.get(mid)
                if not msg or getattr(msg, "empty", False):
                    continue

                if msg.photo and not (msg.document or msg.video or msg.audio or msg.animation):
                    try:
                        db_ch = getattr(Config, "DB_CHANNEL", None)
                        if db_ch:
                            try:
                                copied = await msg.copy(int(db_ch))
                                if copied and copied.photo:
                                    pending_poster = copied.photo.file_id
                                else:
                                    pending_poster = msg.photo.file_id
                            except Exception:
                                pending_poster = await _stabilize_poster(bot, msg.photo.file_id)
                        else:
                            pending_poster = msg.photo.file_id
                    except Exception:
                        pass
                    continue

                media = msg.document or msg.video or msg.audio or msg.animation
                if not media:
                    continue

                file_name = (
                    getattr(media, "file_name", None)
                    or (msg.caption or "")[:80]
                    or f"file_{mid}"
                )
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
                    except Exception as e:
                        logger.warning("find_movie: %s", e)
                    if existing and existing.get("movie_id"):
                        movie_id = existing["movie_id"]
                    else:
                        poster_file_id = await _extract_poster_id(bot, msg) or pending_poster
                        pending_poster = None
                        try:
                            doc = await db.create_movie(
                                title=title,
                                normalized_title=norm,
                                year=year,
                                poster_file_id=poster_file_id,
                            )
                            movie_id = doc.get("movie_id") if doc else None
                        except Exception as e:
                            logger.warning("create_movie: %s", e)
                            movie_id = None
                    if movie_id:
                        bucket[key] = movie_id

                if not movie_id:
                    continue

                try:
                    mov = await db.get_movie(movie_id)
                    if mov and not mov.get("poster_file_id"):
                        pid = await _extract_poster_id(bot, msg)
                        if not pid and pending_poster:
                            pid = pending_poster
                            pending_poster = None
                        if pid:
                            if getattr(db, "movies_col", None) is not None:
                                await db.movies_col.update_one(
                                    {"movie_id": movie_id}, {"$set": {"poster_file_id": pid}}
                                )
                            if getattr(db, "_mem_movies", None) is not None and movie_id in db._mem_movies:
                                db._mem_movies[movie_id]["poster_file_id"] = pid
                except Exception:
                    pass

                cap_txt = f"{title}"
                if year:
                    cap_txt += f" ({year})"
                cap_txt += f"\n{file_name}\nQuality: {final_quality}"
                file_id, channel_msg_id = await _copy_to_db(bot, msg, caption_text=cap_txt)
                if not file_id:
                    logger.warning("no file_id for msg %s", mid)
                    continue

                quality_data = {
                    "file_id": file_id,
                    "file_name": file_name,
                    "file_size": filesize,
                    "file_size_formatted": format_size(filesize) if filesize else "",
                    "label": quality_key,
                    "quality": final_quality,
                    "source_channel_id": channel_id,
                    "source_msg_id": mid,
                    "channel_msg_id": channel_msg_id,
                    "caption": cap_txt,
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
                    logger.warning("post/update: %s", e)

                await asyncio.sleep(0.08)

            msg_id += BATCH

        for k, mid in list(bucket.items()):
            if mid not in posted_set:
                result = await _post_movie_to_group(bot, mid, force_new=True)
                if result == "new":
                    posted += 1
                    posted_set.add(mid)
                elif result:
                    posted_set.add(mid)

        if status_msg_id:
            if _CANCEL.get(admin_id):
                await _status(
                    bot, status_chat_id, status_msg_id,
                    f"⏹ Cancelled\nScanned: {scanned}/{total}\nSaved: {saved}\nPosted: {posted}",
                    show_cancel=False,
                )
            else:
                await _status(
                    bot, status_chat_id, status_msg_id,
                    f"✅ Done\nScanned: {scanned}/{total}\nSaved: {saved}\nPosted: {posted}",
                    show_cancel=False,
                )
    except Exception as e:
        logger.exception("run_indexing: %s", e)
        if status_msg_id:
            await _status(
                bot, status_chat_id, status_msg_id,
                f"❌ Error: {type(e).__name__}: {e}",
                show_cancel=False,
            )
    finally:
        _RUNNING[admin_id] = False
        _CANCEL[admin_id] = False
