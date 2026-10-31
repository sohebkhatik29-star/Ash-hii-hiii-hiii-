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
