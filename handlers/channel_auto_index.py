# handlers/channel_auto_index.py
# Auto-index: watch channel — fast save + group post
# Sirf isi file me logic.

from __future__ import annotations

import asyncio
import logging
import re
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

SETTLE_SECONDS = int(__import__("os").environ.get("AUTO_INDEX_SETTLE_SEC", "15") or 15)

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
    if not raw:
        return ""
    t = str(raw)
    t = re.sub(r"@\w+", " ", t)
    t = re.sub(
        r"(?i)\b(toonworld4all|toonworld|movies_\d+|ash[_\s]?movies?|movies[_\s]?hub|cine[_\s]?hub|netflix_\d+|filmcity|mirror[_\s]?leech|movies[_\s]?1782)\b",
        " ", t,
    )
    t = re.sub(
        r"(?i)\b(480p|720p|1080p|2160p|4k|hdrip|web[- ]?dl|bluray|brrip|hdcam|camrip|dvdrip|x264|x265|hevc|aac|dts|dd5\.?1|dd2\.?0|10bit|8bit|multi\s*audio|esub|dual\s*audio|mkv|mp4|avi|amzn|nf|dsnp|webdl)\b",
        " ", t,
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


def _detect_quality(file_name: str, caption: str, meta_q: str = "") -> str:
    blob = f"{file_name or ''} {caption or ''} {meta_q or ''}"
    for pat, label in [
        (r"(?i)\b(2160p|4k|uhd)\b", "2160p"),
        (r"(?i)\b(1080p|fhd)\b", "1080p"),
        (r"(?i)\b(720p)\b", "720p"),
        (r"(?i)\b(480p)\b", "480p"),
        (r"(?i)\b(360p|240p|sd)\b", "360p"),
    ]:
        if re.search(pat, blob):
            return label
    if re.search(r"(?i)\bhd\b", blob):
        return "720p"
    return "HD"


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
