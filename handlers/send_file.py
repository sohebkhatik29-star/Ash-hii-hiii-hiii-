# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/send_file.py
# ==============================================================================

import asyncio
import logging
import re
from pyrogram import Client
from pyrogram.types import Message, CallbackQuery
from pyrogram.errors import FloodWait

from configs import Config
from handlers.database import db
from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons

try:
    from handlers import caption_db  # noqa: F401
    from handlers.caption_db import build_delivery_caption, media_meta_from_message
except Exception:
    build_delivery_caption = None
    media_meta_from_message = None

try:
    from handlers.auto_delete import get_bot_file_delete_seconds, build_bot_notice_text
except Exception:
    get_bot_file_delete_seconds = None
    build_bot_notice_text = None

logger = logging.getLogger(__name__)

async def delete_after_delay(message: Message, delay: int = 1800):
    try:
        if not delay or delay <= 0:
            return
        await asyncio.sleep(delay)
        await message.delete()
        logger.info(f"Auto-deleted file message {message.id} after {delay} seconds.")
    except Exception as e:
        logger.debug(f"Auto-delete note: {e}")

async def _bot_delete_delay() -> int:
    try:
        if get_bot_file_delete_seconds:
            return int(await get_bot_file_delete_seconds())
    except Exception:
        pass
    return int(getattr(Config, "AUTO_DELETE_TIME", 1800) or 1800)

async def _bot_notice() -> str:
    try:
        if build_bot_notice_text:
            return await build_bot_notice_text()
    except Exception:
        pass
    sec = await _bot_delete_delay()
    mins = max(1, sec // 60)
    return (
        f"⚠️ **Copyright Protection Alert:**\n\n"
        f"File will be automatically deleted in **{mins} minutes**.\n"
        f"Kripya ise turant apne 'Saved Messages' mein forward kar lein!"
    )

async def _apply_custom_caption(bot: Client, sent_file: Message, source_msg: Message = None):
    if not sent_file or build_delivery_caption is None:
        return
    try:
        fname, fsize, orig = "", "", ""
        if source_msg and media_meta_from_message:
            fname, fsize, orig = media_meta_from_message(source_msg)
        if not fname and media_meta_from_message:
            fname, fsize, orig = media_meta_from_message(sent_file)
        custom = await build_delivery_caption(fname, fsize, orig)
        if custom is None:
            return
        try:
            await sent_file.edit_caption(custom)
        except Exception:
            pass
    except Exception as e:
        logger.debug("custom caption: %s", e)

async def send_movie_card(bot: Client, user_id: int, movie_id: str, is_admin: bool = False):
    movie = await db.get_movie(movie_id)
    if not movie:
        await bot.send_message(
            chat_id=user_id,
            text="❌ **Movie Not Found!**\n\nYeh movie database se remove kar di gayi hai ya link invalid hai."
        )
        return

    card_text = build_final_movie_card_text(movie)
    card_buttons = build_final_movie_card_buttons(movie)

    poster = movie.get("poster_file_id")
    if not poster and movie.get("qualities") and Config.DB_CHANNEL:
        try:
            from handlers.poster import extract_poster_from_message
            first_q = list(movie["qualities"].values())[0]
            channel_msg_id = first_q.get("channel_msg_id")
            if channel_msg_id:
                db_msg = await bot.get_messages(Config.DB_CHANNEL, channel_msg_id)
                extracted_poster, _ = await extract_poster_from_message(bot, db_msg)
                if extracted_poster:
                    poster = extracted_poster
                    await db.update_movie_poster(movie_id, poster, "on_demand_extract")
        except Exception as e:
            logger.debug(f"On-demand poster extraction in send_movie_card: {e}")

    if poster:
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=poster,
                caption=card_text,
                reply_markup=card_buttons
            )
            return
        except Exception as e:
            logger.warning(f"Failed to send poster photo: {e}")

    await bot.send_message(
        chat_id=user_id,
        text=card_text,
        reply_markup=card_buttons
    )

def find_matching_quality(qualities: dict, quality_query: str):
    if not qualities or not quality_query:
        return None, None
    if quality_query in qualities:
        return quality_query, qualities[quality_query]
    norm_query = re.sub(r"[^a-zA-Z0-9]", "", quality_query).lower()
    for q_name, q_val in qualities.items():
        norm_name = re.sub(r"[^a-zA-Z0-9]", "", q_name).lower()
        if norm_name == norm_query:
            return q_name, q_val
    return None, None

async def deliver_movie_quality(bot: Client, query: CallbackQuery, movie_id: str, quality: str):
    user_id = query.from_user.id
    movie = await db.get_movie(movie_id)

    if not movie:
        await query.answer("Movie record nahi mila!", show_alert=True)
        return

    qualities = movie.get("qualities", {})
    matched_q_name, q_data = find_matching_quality(qualities, quality)

    if not q_data:
        await query.answer(f"Quality '{quality}' is movie ke liye uplabdh nahi hai!", show_alert=True)
        return

    channel_msg_id = q_data.get("channel_msg_id")
    if not channel_msg_id:
        await query.answer("File reference missing!", show_alert=True)
        return

    await query.answer("Sending your file... please wait!", show_alert=False)

    try:
        sent_file = None
        source_msg = None
        if channel_msg_id and Config.DB_CHANNEL:
            try:
                try:
                    source_msg = await bot.get_messages(Config.DB_CHANNEL, channel_msg_id)
                except Exception:
                    source_msg = None
                custom_cap = None
                if build_delivery_caption and source_msg and media_meta_from_message:
                    fname, fsize, orig = media_meta_from_message(source_msg)
                    custom_cap = await build_delivery_caption(fname, fsize, orig)
                if Config.FORWARD_AS_COPY:
                    kw = dict(
                        chat_id=user_id,
                        from_chat_id=Config.DB_CHANNEL,
                        message_id=channel_msg_id,
                    )
                    if custom_cap is not None:
                        kw["caption"] = custom_cap
                    sent_file = await bot.copy_message(**kw)
                else:
                    sent_file = await bot.forward_messages(
                        chat_id=user_id,
                        from_chat_id=Config.DB_CHANNEL,
                        message_ids=channel_msg_id
                    )
                    if custom_cap is not None:
                        await _apply_custom_caption(bot, sent_file, source_msg)
            except Exception as e:
                logger.warning(f"Channel copy failed: {e}")

        if not sent_file and q_data.get("file_id"):
            sent_file = await bot.send_cached_media(
                chat_id=user_id,
                file_id=q_data["file_id"]
            )
            await _apply_custom_caption(bot, sent_file, source_msg)

        if not sent_file:
            await query.answer("File deliver karne mein samasya aayi!", show_alert=True)
            return

        delay = await _bot_delete_delay()
        notice_text = await _bot_notice()
        notice = await bot.send_message(
            chat_id=user_id,
            text=notice_text,
            reply_to_message_id=sent_file.id
        )

        asyncio.create_task(delete_after_delay(sent_file, delay))
        asyncio.create_task(delete_after_delay(notice, delay))

    except FloodWait as sl:
        await asyncio.sleep(sl.value)
        await deliver_movie_quality(bot, query, movie_id, quality)
    except Exception as e:
        logger.error(f"Error sending movie quality: {e}")
        await bot.send_message(chat_id=user_id, text=f"File bhejne mein error aaya: {e}")

async def deliver_movie_quality_direct(bot: Client, user_id: int, movie_id: str, quality: str):
    movie = await db.get_movie(movie_id)
    if not movie:
        await bot.send_message(chat_id=user_id, text="❌ **Movie record nahi mila ya link invalid hai!**")
        return

    qualities = movie.get("qualities", {})
    matched_q_name, q_data = find_matching_quality(qualities, quality)
    if not q_data:
        await bot.send_message(chat_id=user_id, text=f"❌ **Quality '{quality}' is movie ke liye uplabdh nahi hai!**")
        return

    channel_msg_id = q_data.get("channel_msg_id")

    try:
        sent_file = None
        source_msg = None
        if channel_msg_id and Config.DB_CHANNEL:
            try:
                try:
                    source_msg = await bot.get_messages(Config.DB_CHANNEL, channel_msg_id)
                except Exception:
                    source_msg = None
                custom_cap = None
                if build_delivery_caption and source_msg and media_meta_from_message:
                    fname, fsize, orig = media_meta_from_message(source_msg)
                    custom_cap = await build_delivery_caption(fname, fsize, orig)
                if Config.FORWARD_AS_COPY:
                    kw = dict(
                        chat_id=user_id,
                        from_chat_id=Config.DB_CHANNEL,
                        message_id=channel_msg_id,
                    )
                    if custom_cap is not None:
                        kw["caption"] = custom_cap
                    sent_file = await bot.copy_message(**kw)
                else:
                    sent_file = await bot.forward_messages(
                        chat_id=user_id,
                        from_chat_id=Config.DB_CHANNEL,
                        message_ids=channel_msg_id
                    )
                    if custom_cap is not None:
                        await _apply_custom_caption(bot, sent_file, source_msg)
            except Exception as e:
                logger.warning(f"Direct channel copy failed: {e}")

        if not sent_file and q_data.get("file_id"):
            sent_file = await bot.send_cached_media(
                chat_id=user_id,
                file_id=q_data["file_id"]
            )
            await _apply_custom_caption(bot, sent_file, source_msg)

        if not sent_file:
            await bot.send_message(chat_id=user_id, text="❌ **File deliver nahi ho paayi.**")
            return

        delay = await _bot_delete_delay()
        notice_text = await _bot_notice()
        notice = await bot.send_message(
            chat_id=user_id,
            text=notice_text,
            reply_to_message_id=sent_file.id
        )

        asyncio.create_task(delete_after_delay(sent_file, delay))
        asyncio.create_task(delete_after_delay(notice, delay))

    except FloodWait as sl:
        await asyncio.sleep(sl.value)
        await deliver_movie_quality_direct(bot, user_id, movie_id, quality)
    except Exception as e:
        logger.error(f"Error direct sending movie quality: {e}")
        await bot.send_message(chat_id=user_id, text=f"File bhejne mein error aaya: {e}")

async def send_media_and_reply(bot: Client, user_id: int, file_id: int):
    try:
        source_msg = None
        custom_cap = None
        try:
            source_msg = await bot.get_messages(Config.DB_CHANNEL, file_id)
            if build_delivery_caption and media_meta_from_message and source_msg:
                fname, fsize, orig = media_meta_from_message(source_msg)
                custom_cap = await build_delivery_caption(fname, fsize, orig)
        except Exception:
            pass

        if Config.FORWARD_AS_COPY:
            kw = dict(chat_id=user_id, from_chat_id=Config.DB_CHANNEL, message_id=file_id)
            if custom_cap is not None:
                kw["caption"] = custom_cap
            sent = await bot.copy_message(**kw)
        else:
            sent = await bot.forward_messages(chat_id=user_id, from_chat_id=Config.DB_CHANNEL, message_ids=file_id)
            if custom_cap is not None:
                await _apply_custom_caption(bot, sent, source_msg)

        delay = await _bot_delete_delay()
        notice_text = await _bot_notice()
        notice = await bot.send_message(
            chat_id=user_id,
            text=notice_text,
            reply_to_message_id=sent.id
        )
        asyncio.create_task(delete_after_delay(sent, delay))
        asyncio.create_task(delete_after_delay(notice, delay))
    except Exception as e:
        logger.error(f"Legacy file send error: {e}")
