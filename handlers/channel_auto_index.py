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

# Wait long enough so 480/720/1080 all arrive before first group post
# 12s = fast enough for group post, still covers multi-quality forward gaps
SETTLE_SECONDS = int(__import__("os").environ.get("AUTO_INDEX_SETTLE_SEC", "12") or 12)

_pending_finalize: Dict[str, asyncio.Task] = {}
_key_to_movie: Dict[str, str] = {}
_first_seen: Dict[str, float] = {}  # movie_key -> earliest channel msg timestamp
_ready: Dict[str, str] = {}  # movie_key -> movie_id (settle done, waiting post order)
_lock = asyncio.Lock()
_posting = False
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
        r"mkv|mp4|avi|amzn|nf|dsnp|webdl"
        r")\b",
        " ",
        t,
    )
    t = re.sub(r"(?i)\b(hindi|english|tamil|telugu)\b", " ", t)
    t = re.sub(r"[\[\]\{\}|_]+", " ", t)
    t = re.sub(r"[\(\)]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
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
    t = re.sub(r"\b(the|a|an)\b", " ", t)
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

    # Soft match ONLY same year + exact core title (no full-DB scan, no substring)
    # "Little Krishna Movie 2" aur "Little Krishna Movie 3" alag rahenge
    if not existing and getattr(db, "movies_col", None) is not None and year_val is not None:
        try:
            core = _core_title(title)
            if core and len(core) > 6:
                cursor = db.movies_col.find({"year": year_val}).sort("added_at", -1).limit(80)
                async for doc in cursor:
                    dcore = _core_title(doc.get("title") or doc.get("normalized_title") or "")
                    if core and dcore and core == dcore:
                        existing = doc
                        logger.info(
                            "auto_index soft-match title=%s -> existing=%s (%s)",
                            title, doc.get("title"), doc.get("movie_id"),
                        )
                        break
        except Exception as e:
            logger.debug("soft match: %s", e)

    if existing and existing.get("movie_id"):
        mid = existing["movie_id"]
        try:
            old_t = (existing.get("title") or "").strip()
            if title and len(title) > len(old_t) and getattr(db, "movies_col", None) is not None:
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
