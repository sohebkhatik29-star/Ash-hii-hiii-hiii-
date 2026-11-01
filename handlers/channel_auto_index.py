# handlers/channel_auto_index.py
# Auto-index: watch channel — fast save + group post (addmovie jaisa feel)
# Sirf isi file me logic.

from __future__ import annotations

import asyncio
import logging
import re
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message

from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size

logger = logging.getLogger("ChannelAutoIndex")

_DEFAULT_CH = "-1004397160844"
WATCH_CHANNEL_ID = int(
    __import__("os").environ.get("AUTO_INDEX_CHANNEL_ID")
    or getattr(Config, "AUTO_INDEX_CHANNEL_ID", None)
    or _DEFAULT_CH
)

# Wait so 480/720/1080 collect, fast (~5s, reliable multi-quality)
SETTLE_SECONDS = int(__import__("os").environ.get("AUTO_INDEX_SETTLE_SEC", "1") or 1)

_pending_finalize: Dict[str, asyncio.Task] = {}
_key_to_movie: Dict[str, str] = {}
_lock = asyncio.Lock()
_stats = {"seen": 0, "saved": 0, "posted": 0, "errors": 0}
_seen_msgs: Dict[int, float] = {}


def _safe(text: Any, fallback: str = "") -> str:
    try:
        s = str(text if text is not None else fallback)
        return s.encode("utf-8", "surrogatepass").decode("utf-8", "ignore")
    except Exception:
        return fallback


def _clean_title(raw: str) -> str:
    """Clean channel brand/quality noise but KEEP 'Movie 2' / Part N in title."""
    if not raw:
        return ""
    t = str(raw)
    t = re.sub(r"@\w+", " ", t)
    # channel brands only (NOT generic 'movie 2' — that is part of title)
    t = re.sub(
        r"(?i)\b("
        r"toonworld4all|toonworld|movies_\d+|ash[_\s]?movies?|"
        r"movies[_\s]?hub|cine[_\s]?hub|netflix_\d+|filmcity|"
        r"mirror[_\s]?leech|movies[_\s]?1782"
        r")\b",
        " ",
        t,
    )
    t = re.sub(
        r"(?i)\b("
        r"480p|720p|1080p|2160p|4k|hdrip|web[- ]?dl|bluray|brrip|"
        r"hdcam|camrip|dvdrip|x264|x265|hevc|aac|dts|dd5\.?1|"
        r"dd2\.?0|10bit|8bit|multi\s*audio|esub|dual\s*audio|"
        r"mkv|mp4|avi|amzn|nf|dsnp|webdl|org\.?|proper|repack|"
        r"eng\s*hin|hin\s*eng|dual|msubs|subs?"
        r")\b",
        " ",
        t,
    )
    t = re.sub(r"(?i)\b(hindi|english|tamil|telugu|or)\b", " ", t)
    t = re.sub(r"[\[\]\{\}|_]+", " ", t)
    t = re.sub(r"[\(\)]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # drop trailing year if it is alone (we keep year separately)
    t = re.sub(r"\s+(19|20)\d{2}\s*$", "", t).strip()
    return t


def _movie_key(title: str, year: Any) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    y = str(year or "").strip()
    return f"{t}|{y}"


def _core_title(title: str) -> str:
    """Match key: 'Movie 2' / Part N hata ke core name (same film merge)."""
    t = (title or "").lower()
    t = re.sub(r"\bmovie\s*\d*\b", " ", t)
    t = re.sub(r"\bpart\s*\d+\b", " ", t)
    t = re.sub(r"\b(the|a|an|eng|hin|org)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _detect_quality(file_name: str, caption: str, meta_q: str = "") -> str:
    blob = f"{file_name or ''} {caption or ''} {meta_q or ''}"
    checks = [
        (r"(?i)\b(2160p|4k|uhd)\b", "2160p"),
        (r"(?i)\b(1080p|fhd)\b", "1080p"),
        (r"(?i)\b(720p)\b", "720p"),
        (r"(?i)\b(480p)\b", "480p"),
        (r"(?i)\b(360p|240p|sd)\b", "360p"),
    ]
    for pat, label in checks:
        if re.search(pat, blob):
            return label
    if re.search(r"(?i)\bhd\b", blob):
        return "720p"
    return "HD"


def _quality_key(q: str, meta: dict) -> str:
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
    targets = []
    log_ch = getattr(Config, "LOG_CHANNEL", None)
    owner = getattr(Config, "BOT_OWNER", None)
    if log_ch:
        targets.append(int(log_ch))
    if owner:
        try:
            targets.append(int(owner))
        except Exception:
            pass
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
        sent = await message.copy(int(db_ch), caption=cap if cap else None)
        m = sent.document or sent.video or sent.audio or sent.animation
        return (getattr(m, "file_id", None) or orig_fid), (sent.id if sent else None)
    except FloodWait as e:
        await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
        try:
            sent = await message.copy(int(db_ch), caption=cap if cap else None)
            m = sent.document or sent.video or sent.audio or sent.animation
            return (getattr(m, "file_id", None) or orig_fid), (sent.id if sent else None)
        except Exception:
            return orig_fid, None
    except Exception as e:
        logger.warning("copy_to_db: %s", e)
        return orig_fid, None


async def _get_or_create_movie(title: str, year: Any, language: str = None) -> Optional[str]:
    title = _clean_title(_safe(title)) or "Unknown"
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

    # Soft match: same core title (year flexible ±2 so 2021/2022 variants merge)
    # Weak titles (Untitled) → attach to most recently updated movie (multi-quality batch)
    if not existing and getattr(db, "movies_col", None) is not None:
        try:
            core = _core_title(title)
            is_weak = (not core) or core in ("unknown", "untitled", "untitled movie", "movie") or len(core) < 4
            best = None
            best_ts = ""
            cursor = db.movies_col.find({}).sort("updated_at", -1).limit(300)
            async for doc in cursor:
                dtitle = doc.get("title") or doc.get("normalized_title") or ""
                dcore = _core_title(dtitle)
                if is_weak:
                    # ONLY attach to non-weak movie updated in last 90 seconds (same batch)
                    if not dcore or dcore in ("unknown", "untitled", "movie"):
                        continue
                    ts_raw = doc.get("updated_at") or doc.get("added_at")
                    if not ts_raw:
                        continue
                    try:
                        from datetime import datetime, timezone
                        if hasattr(ts_raw, "timestamp"):
                            age = datetime.now(timezone.utc).timestamp() - ts_raw.timestamp()
                        else:
                            # iso string
                            s = str(ts_raw).replace("Z", "+00:00")
                            dt = datetime.fromisoformat(s)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            age = datetime.now(timezone.utc).timestamp() - dt.timestamp()
                        if age > 90:
                            continue
                    except Exception:
                        continue
                    ts = str(ts_raw)
                    if best is None or ts >= best_ts:
                        best = doc
                        best_ts = ts
                    if best is not None:
                        break
                    continue
                if not dcore or len(dcore) < 4:
                    continue
                if not (core == dcore or (len(core) >= 6 and (core in dcore or dcore in core))):
                    continue
                dy = doc.get("year")
                year_ok = True
                if year_val is not None and dy is not None:
                    try:
                        year_ok = abs(int(dy) - int(year_val)) <= 2
                    except Exception:
                        year_ok = True
                if not year_ok:
                    continue
                ts = str(doc.get("updated_at") or doc.get("added_at") or "")
                if best is None or ts >= best_ts:
                    best = doc
                    best_ts = ts
            if best and best.get("movie_id"):
                existing = best
                logger.info(
                    "auto_index soft-match title=%s -> existing=%s (%s) weak=%s",
                    title, best.get("title"), best.get("movie_id"), is_weak,
                )
        except Exception as e:
            logger.debug("soft match: %s", e)

    if existing and existing.get("movie_id"):
        mid = existing["movie_id"]
        # Prefer longer/cleaner display title (never overwrite good title with Untitled)
        try:
            old_t = (existing.get("title") or "").strip()
            weak_new = (not title) or title.lower().strip() in (
                "unknown", "untitled", "untitled movie", "movie", ""
            )
            if (
                title
                and not weak_new
                and len(title) > len(old_t)
                and getattr(db, "movies_col", None) is not None
            ):
                await db.movies_col.update_one(
                    {"movie_id": mid},
                    {"$set": {
                        "title": title,
                        "normalized_title": re.sub(r"[^\w\s]", "", title.lower()).strip(),
                    }},
                )
        except Exception:
            pass
        return mid

    # NEVER create a new movie from weak/Untitled titles — causes split posts
    core_now = _core_title(title)
    is_weak_now = (
        (not core_now)
        or core_now in ("unknown", "untitled", "untitled movie", "movie")
        or len(core_now) < 4
    )
    if is_weak_now:
        # attach to any movie currently settling (same multi-forward batch)
        for mid in list(_key_to_movie.values()):
            if mid:
                logger.info(
                    "auto_index weak title attach to pending movie_id=%s title=%s",
                    mid, title,
                )
                return mid
        logger.warning(
            "auto_index skip create for weak title=%s (no recent match)",
            title,
        )
        return None

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


async def _stabilize_poster(bot: Client, poster_file_id: str) -> Optional[str]:
    """Thumb/document id ko real photo file_id banata hai (DB channel pe re-upload)."""
    if not poster_file_id:
        return None
    db_ch = getattr(Config, "DB_CHANNEL", None)
    if not db_ch:
        return poster_file_id
    try:
        data = await bot.download_media(poster_file_id, in_memory=True)
        if data is None:
            raise RuntimeError("download empty")
        if hasattr(data, "getvalue"):
            raw = data.getvalue()
        else:
            raw = data.read() if hasattr(data, "read") else bytes(data)
        if raw:
            bio = BytesIO(raw)
            bio.name = "poster.jpg"
            sent = await bot.send_photo(int(db_ch), photo=bio)
            return sent.photo.file_id if sent and sent.photo else poster_file_id
    except Exception as e:
        logger.warning("stabilize download: %s", e)
    try:
        sent = await bot.send_photo(int(db_ch), photo=poster_file_id)
        return sent.photo.file_id if sent and sent.photo else poster_file_id
    except Exception:
        return poster_file_id


async def _set_poster_from_message(bot: Client, movie_id: str, message: Message, media) -> None:
    """Poster: photo ya video/document thumb → stabilize → save."""
    try:
        movie = await db.get_movie(movie_id)
        if movie and movie.get("poster_file_id"):
            return
        fid = None
        if message.photo:
            fid = message.photo.file_id
        if not fid and media:
            thumbs = getattr(media, "thumbs", None)
            if thumbs:
                t = thumbs[-1] if isinstance(thumbs, list) else thumbs
                fid = getattr(t, "file_id", None)
        if not fid:
            return
        stable = await _stabilize_poster(bot, fid)
        if not stable:
            return
        if hasattr(db, "update_movie_poster"):
            try:
                await db.update_movie_poster(movie_id, stable, "channel_thumb")
                return
            except Exception:
                pass
        if getattr(db, "movies_col", None) is not None:
            await db.movies_col.update_one(
                {"movie_id": movie_id},
                {"$set": {"poster_file_id": stable}},
            )
    except Exception as e:
        logger.debug("poster set: %s", e)


async def _post_or_update_group(bot: Client, movie_id: str) -> bool:
    """Hamesha MOVIE_GROUP_ID me NAYA post. Purana delete. Silent edit nahi."""
    from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons

    movie = await db.get_movie(movie_id)
    if not movie:
        return False
    target = getattr(Config, "MOVIE_GROUP_ID", None)
    if not target:
        logger.warning("MOVIE_GROUP_ID not set")
        return False
    target = int(target)

    card_text = build_final_movie_card_text(movie)
    buttons = build_final_movie_card_buttons(movie, for_group=True)
    poster_id = movie.get("poster_file_id")
    g_chat, g_msg = movie.get("group_chat_id"), movie.get("group_msg_id")

    # Purana group post delete — sirf same target group
    if g_chat and g_msg:
        try:
            if int(g_chat) == target:
                await bot.delete_messages(target, int(g_msg))
        except Exception as e:
            logger.debug("delete old group post: %s", e)

    sent = None
    last_err = None

    if poster_id:
        try:
            sent = await bot.send_photo(
                target,
                photo=poster_id,
                caption=_safe(card_text),
                reply_markup=buttons,
            )
        except Exception as e:
            last_err = e
            logger.warning("send_photo fail: %s", e)
            try:
                stable = await _stabilize_poster(bot, poster_id)
                if stable:
                    if getattr(db, "movies_col", None) is not None:
                        await db.movies_col.update_one(
                            {"movie_id": movie_id},
                            {"$set": {"poster_file_id": stable}},
                        )
                    sent = await bot.send_photo(
                        target,
                        photo=stable,
                        caption=_safe(card_text),
                        reply_markup=buttons,
                    )
            except Exception as e2:
                last_err = e2
                logger.warning("stabilize+photo fail: %s", e2)
                sent = None

    if not sent:
        try:
            sent = await bot.send_message(
                target,
                text=_safe(card_text),
                reply_markup=buttons,
                disable_web_page_preview=True,
            )
        except FloodWait as e:
            await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
            try:
                sent = await bot.send_message(
                    target,
                    text=_safe(card_text),
                    reply_markup=buttons,
                    disable_web_page_preview=True,
                )
            except Exception as e2:
                last_err = e2
                logger.warning("group post flood retry: %s", e2)
                return False
        except Exception as e:
            last_err = e
            logger.warning("group send_message: %s", e)
            return False

    if not sent:
        logger.warning("group post failed movie=%s err=%s", movie_id, last_err)
        return False

    real_link = getattr(sent, "link", None) or ""
    try:
        await db.update_movie_group_post(movie_id, sent.chat.id, sent.id)
        if real_link and "t.me" in str(real_link):
            if getattr(db, "movies_col", None) is not None:
                await db.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {"group_post_link": str(real_link).strip()}},
                )
    except Exception as e:
        logger.warning("update_movie_group_post: %s", e)

    logger.info(
        "group NEW post movie=%s chat=%s msg=%s link=%s",
        movie_id, sent.chat.id, sent.id, real_link,
    )
    _post_or_update_group.last_link = str(real_link) if real_link else f"chat={sent.chat.id} msg={sent.id}"
    _post_or_update_group.last_chat = int(sent.chat.id)
    return True


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
        link = getattr(_post_or_update_group, "last_link", "") or ""
        chat = getattr(_post_or_update_group, "last_chat", "") or ""

        # Near-duplicate cleanup: same core title+year, different movie_id → delete extra
        if ok and getattr(db, "movies_col", None) is not None and hasattr(db, "delete_movie"):
            try:
                core = _core_title(title)
                y = (movie or {}).get("year")
                async for doc in db.movies_col.find({}):
                    other = doc.get("movie_id")
                    if not other or other == movie_id:
                        continue
                    if y is not None:
                        try:
                            if doc.get("year") is None or int(doc.get("year")) != int(y):
                                continue
                        except Exception:
                            continue
                    ocore = _core_title(doc.get("title") or "")
                    if core and ocore and (core == ocore or core in ocore or ocore in core):
                        try:
                            await db.delete_movie(other)
                            logger.info("auto_index deleted duplicate movie_id=%s title=%s", other, doc.get("title"))
                        except Exception as de:
                            logger.debug("dup delete: %s", de)
            except Exception as ce:
                logger.debug("dup cleanup: %s", ce)

        await _notify(
            bot,
            f"{'✅' if ok else '⚠️'} Auto-index {'POSTED' if ok else 'POST FAIL'}\n"
            f"🎬 {title}\n"
            f"📦 Qualities: {', '.join(qs) if qs else '-'}\n"
            f"{'Group NEW post' if ok else 'Group post fail'}\n"
            f"chat={chat}\n{link}",
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
    msg_id = int(message.id)

    import time as _time
    now = _time.time()
    if msg_id in _seen_msgs and (now - _seen_msgs[msg_id]) < 120:
        return
    _seen_msgs[msg_id] = now
    if len(_seen_msgs) > 500:
        cutoff = now - 300
        for k in [k for k, v in _seen_msgs.items() if v < cutoff]:
            _seen_msgs.pop(k, None)

    _stats["seen"] += 1
    logger.info(
        "auto_index SEEN chat=%s msg=%s type=%s",
        chat_id, msg_id, getattr(message.chat, "type", None),
    )

    if chat_id != int(WATCH_CHANNEL_ID):
        return

    media = message.document or message.video or message.audio or message.animation
    if not media:
        return

    file_name = getattr(media, "file_name", None) or ""
    file_size = int(getattr(media, "file_size", 0) or 0)
    caption = message.caption or ""

    try:
        meta = extract_movie_metadata(file_name, caption)
    except Exception:
        meta = {}

    # Build best raw title from caption / filename / parser (prefer human caption)
    cap0 = (caption.split("\n")[0] if caption else "").strip()
    fname0 = (file_name.rsplit(".", 1)[0] if file_name else "").strip()
    meta_title = _safe(meta.get("title") or "")
    # Prefer caption, then filename, then parser — parser often returns Untitled
    candidates = [cap0, fname0, meta_title]
    raw_title = "Unknown"
    for c in candidates:
        if not c:
            continue
        cl = c.lower().strip()
        if cl in ("unknown", "untitled", "untitled movie", ""):
            continue
        raw_title = c
        break
    if raw_title == "Unknown":
        raw_title = cap0 or fname0 or meta_title or "Unknown"

    title = _clean_title(raw_title) or raw_title
    # second pass: if still weak, strip quality tokens manually from longest source
    if not title or title.lower() in ("unknown", "untitled", "untitled movie", ""):
        blob = cap0 or fname0 or meta_title
        blob = re.sub(
            r"(?i)\b(480p|720p|1080p|2160p|4k|hdrip|web[- ]?dl|bluray|brrip|"
            r"hdcam|camrip|dvdrip|x264|x265|hevc|aac|dts|dd5\.?1|dd2\.?0|"
            r"10bit|8bit|multi\s*audio|esub|dual\s*audio|mkv|mp4|avi|"
            r"amzn|nf|dsnp|webdl|org\.?|proper|repack|eng\s*hin|hin\s*eng|"
            r"dual|msubs|subs?|hindi|english|tamil|telugu)\b",
            " ",
            blob or "",
        )
        blob = re.sub(r"[\[\]\{\}|_\(\)]+", " ", blob)
        blob = re.sub(r"\s+(19|20)\d{2}\s*", " ", blob)
        blob = re.sub(r"\s+", " ", blob).strip()
        title = blob or "Unknown"

    year = meta.get("year")
    if not year:
        blob = f"{file_name} {caption}"
        m = re.search(r"[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]", blob)
        if not m:
            m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", blob)
        if m:
            year = m.group(1)
    language = meta.get("language") or "Hindi"

    q = _detect_quality(file_name, caption, meta.get("quality") or "")
    q_key = _quality_key(q, meta)

    weak_title = (not title) or title.lower().strip() in (
        "unknown", "untitled", "untitled movie", "movie", ""
    )
    if weak_title:
        logger.warning(
            "auto_index weak title msg=%s fname=%s cap=%s — will soft-match recent",
            msg_id, (file_name or "")[:60], (caption or "")[:60],
        )
        title = title if title and title.lower() not in ("unknown", "") else "Unknown"

    file_id, db_msg_id = await _copy_to_db(bot, message, caption_text=caption or file_name)
    if not file_id:
        file_id = getattr(media, "file_id", None)
    if not file_id:
        _stats["errors"] += 1
        logger.warning("auto_index no file_id msg=%s", msg_id)
        return

    movie_id = await _get_or_create_movie(title, year, language=language)
    if not movie_id:
        # weak/Untitled with no batch to attach → skip quietly (not a hard error)
        logger.warning("auto_index no movie_id for title=%s msg — skipped", title)
        return

    # Order lock: indexing channel me jo movie pehle aayi uska added_at earliest rakho
    # → search results me pehle wali movie hamesha upar aaye
    try:
        if getattr(db, "movies_col", None) is not None and message.date:
            iso = message.date.isoformat() if hasattr(message.date, "isoformat") else str(message.date)
            mdoc = await db.get_movie(movie_id)
            cur = str((mdoc or {}).get("added_at") or "")
            locked = bool((mdoc or {}).get("_order_locked"))
            # pehli quality / earlier message → set + lock; later messages mat overwrite karo
            if not locked or (cur and iso < cur):
                await db.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {"added_at": iso, "_order_locked": True}},
                )
    except Exception as e:
        logger.debug("order lock: %s", e)

    quality_data = {
        "file_id": file_id,
        "file_name": _safe(file_name),
        "file_size": file_size,
        "file_size_formatted": format_size(file_size) if file_size else "?",
        "label": q_key,
        "quality": q_key,
        "season": meta.get("season") or None,
        "episode": meta.get("episode") or None,
        "db_msg_id": db_msg_id,
        "channel_msg_id": db_msg_id,
        "source_chat_id": chat_id,
        "source_msg_id": msg_id,
    }
    try:
        await db.add_quality_to_movie(movie_id, q_key, quality_data)
    except Exception as e:
        _stats["errors"] += 1
        logger.error("add_quality: %s", e)
        return

    _stats["saved"] += 1
    await _set_poster_from_message(bot, movie_id, message, media)

    # Always settle by movie_id so all qualities (even weak titles) share ONE timer
    mkey = str(movie_id)
    qcount = 0
    try:
        mdoc = await db.get_movie(movie_id)
        qcount = len((mdoc or {}).get("qualities") or {})
    except Exception:
        pass
    _schedule_finalize(bot, mkey, movie_id)
    logger.info(
        "auto_index SAVED title=%s year=%s quality=%s movie_id=%s msg=%s qcount=%s settle=%ss",
        title, year, q_key, movie_id, msg_id, qcount, SETTLE_SECONDS,
    )


def register_channel_auto_index(bot: Client):
    """Register watcher for AUTO INDEX channel."""

    @bot.on_message(
        filters.chat(WATCH_CHANNEL_ID)
        & (filters.document | filters.video | filters.audio | filters.animation),
        group=0,
    )
    async def _on_watch_media(client: Client, message: Message):
        try:
            await _handle_channel_media(client, message)
        except Exception as e:
            _stats["errors"] += 1
            logger.exception("auto_index handler: %s", e)
            try:
                await _notify(client, f"❌ Auto-index handler error:\n{type(e).__name__}: {e}")
            except Exception:
                pass

    @bot.on_message(filters.command(["autoindex", "auto_index"]) & filters.private, group=1)
    async def _autoindex_status(client: Client, message: Message):
        pending = len([t for t in _pending_finalize.values() if not t.done()])
        await message.reply_text(
            f"📡 Channel Auto-Index\n"
            f"Watch: <code>{WATCH_CHANNEL_ID}</code>\n"
            f"Settle: {SETTLE_SECONDS}s\n"
            f"Seen: {_stats['seen']} | Saved: {_stats['saved']} | "
            f"Posted: {_stats['posted']} | Err: {_stats['errors']}\n"
            f"Pending finalize: {pending}\n"
            f"MOVIE_GROUP_ID: <code>{getattr(Config, 'MOVIE_GROUP_ID', None)}</code>",
            quote=True,
        )

    logger.info(
        "Channel auto-index ON watch=%s settle=%ss group=%s",
        WATCH_CHANNEL_ID, SETTLE_SECONDS, getattr(Config, "MOVIE_GROUP_ID", None),
        )
