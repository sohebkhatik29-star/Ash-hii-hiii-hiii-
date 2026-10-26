# ============================================================================
# handlers/poster.py  — ONLY poster extraction (alg file)
# Video/document se thumbnail nikaal ke permanent Telegram photo.file_id banata hai
# ============================================================================

import os
import logging
from typing import Optional
from pyrogram import Client
from pyrogram.types import Message
from configs import Config

logger = logging.getLogger("Poster")


def _safe_name(s) -> str:
    if not s:
        return "media"
    try:
        from handlers.parser import sanitize_text
        return sanitize_text(str(s)) or "media"
    except Exception:
        return str(s)[:80]


async def extract_poster_from_message(bot: Client, message: Message) -> Optional[str]:
    """
    Incoming video/document se poster nikaalta hai.
    Returns: photo file_id (str) ya None.

    IMPORTANT: sirf file_id string return karta hai (tuple nahi),
    taaki save_media me poster_file_id = await extract_poster... seedha kaam kare.
    """
    try:
        # 1) Admin ne seedha photo bheja
        if message.photo:
            fid = message.photo.file_id
            logger.info("Poster source=manual_photo")
            return fid

        media = message.video or message.document
        if not media:
            return None

        # 2) Embedded thumbnail (largest)
        thumb = None
        if getattr(media, "thumbs", None):
            thumb = media.thumbs[-1]
        elif getattr(media, "thumb", None):
            thumb = media.thumb

        if not thumb:
            logger.info("No embedded thumbnail on media")
            return None

        target = getattr(Config, "DB_CHANNEL", None) or message.chat.id
        safe = _safe_name(getattr(media, "file_name", None))

        # 3a) Pehle thumb.file_id se direct send_photo try (fast, no download)
        thumb_fid = getattr(thumb, "file_id", None)
        if thumb_fid:
            try:
                sent = await bot.send_photo(
                    chat_id=target,
                    photo=thumb_fid,
                    caption=f"Poster: {safe}",
                )
                if sent and sent.photo:
                    logger.info("Poster source=thumb_file_id ok")
                    return sent.photo.file_id
            except Exception as e:
                logger.debug("thumb_file_id send failed: %s", type(e).__name__)

        # 3b) Download thumb -> re-upload as real photo (permanent file_id)
        logger.info("Downloading thumbnail for %s...", safe)
        thumb_path = await bot.download_media(thumb)
        if not thumb_path or not os.path.exists(thumb_path):
            return None

        try:
            sent = await bot.send_photo(
                chat_id=target,
                photo=thumb_path,
                caption=f"Poster: {safe}",
            )
            if sent and sent.photo:
                logger.info("Poster source=video_thumbnail ok")
                return sent.photo.file_id
        finally:
            try:
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except Exception:
                pass

    except Exception as e:
        logger.warning("[Poster] %s: %s", type(e).__name__, e)

    return None
