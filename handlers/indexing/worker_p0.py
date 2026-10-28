# =============================================================================
# handlers/indexing/worker.py — scan channel range, save movies, post posters
# =============================================================================
import asyncio
import logging
import re
from typing import Optional, Tuple

from pyrogram import Client
from pyrogram.errors import FloodWait

from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size
from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons

logger = logging.getLogger("IndexingWorker")

# admin_id -> cancel flag
_CANCEL: dict = {}
# admin_id -> running flag
_RUNNING: dict = {}


def request_cancel(admin_id: int):
    _CANCEL[int(admin_id)] = True


def is_running(admin_id: int) -> bool:
    return bool(_RUNNING.get(int(admin_id)))


def _clear_flags(admin_id: int):
    _CANCEL.pop(int(admin_id), None)
    _RUNNING.pop(int(admin_id), None)


def _is_cancelled(admin_id: int) -> bool:
    return bool(_CANCEL.get(int(admin_id)))


def _norm_quality(q: str) -> str:
    """Normalize to clean 480p / 720p / 1080p only."""
    if not q:
        return "720p"
    s = str(q).lower().strip()
    if "1080" in s or "1920" in s or "fhd" in s:
        return "1080p"
    if "720" in s or "1280" in s or "hd" in s:
        return "720p"
    if "480" in s or "640" in s or "sd" in s or "360" in s:
        return "480p"
    # fallback by number
    m = re.search(r"(\d{3,4})\s*p?", s)
    if m:
        n = int(m.group(1))
        if n >= 1000:
            return "1080p"
        if n >= 700:
            return "720p"
        return "480p"
    return "720p"


async def _status(bot: Client, chat_id: int, msg_id: int, text: str, show_cancel: bool = True):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = None
    if show_cancel:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="idx_cancel")]])
    try:
        await bot.edit_message_text(chat_id, msg_id, text, reply_markup=kb)
    except Exception:
        try:
            await bot.send_message(chat_id, text, reply_markup=kb)
        except Exception:
            pass


async def _bot_is_admin(client: Client, channel_id: int) -> bool:
    try:
        m = await client.get_chat_member(channel_id, "me")
        st = str(getattr(m.status, "value", m.status) or m.status).lower()
        return any(x in st for x in ("administrator", "owner", "creator"))
    except Exception:
        return False
