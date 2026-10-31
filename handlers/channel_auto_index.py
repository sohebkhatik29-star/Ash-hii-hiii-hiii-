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
SETTLE_SECONDS = int(__import__("os").environ.get("AUTO_INDEX_SETTLE_SEC", "18") or 18)

_pending_finalize: Dict[str, asyncio.Task] = {}
_key_to_movie: Dict[str, str] = {}
_first_seen: Dict[str, float] = {}  # movie_key -> earliest channel msg timestamp
_ready: Dict[str, str] = {}  # movie_key -> movie_id (settle done, waiting post order)
_lock = asyncio.Lock()
_posting = False
_stats = {"seen": 0, "saved": 0, "posted": 0, "errors": 0}
_seen_msgs: Dict[int, float] = {}
