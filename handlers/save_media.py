# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/save_media.py
# Purpose: Handles incoming media strictly within the /addmovie session.
#          Fast path: save file_id + update UI immediately; DB-channel copy
#          runs in background so admin sees instant feedback.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata, format_size
from handlers.poster import extract_poster_from_message
from handlers.session_manager import session_mgr
from handlers.movie_card import build_final_movie_card_text, build_final_movie_card_buttons, generate_movie_links

logger = logging.getLogger(__name__)

_SESSION_MARKUP = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("\u2795 Continue Upload", callback_data="session_continue"),
        InlineKeyboardButton("\ud83d\udd17 Generate Links", callback_data="session_generate"),
    ],
    [InlineKeyboardButton("\u274c Cancel Session", callback_data="session_cancel")],
])


async def forward_to_db_channel(bot: Client, message: Message) -> Message:
    try:
        sent = await message.copy(Config.DB_CHANNEL)
        return sent
    except FloodWait as sl:
        logger.warning(f"FloodWait of {sl.value}s while saving to DB channel")
        await asyncio.sleep(sl.value)
        return await forward_to_db_channel(bot, message)
    except Exception as e:
        logger.error(f"Failed to forward media to DB channel: {e}")
        raise e


async def _bg_copy_and_patch(
    bot: Client,
    message: Message,
    movie_id: str,
    quality_key: str,
    quality_data: dict,
):
    """Copy media to DB channel in background; patch channel_msg_id when done."""
    try:
        channel_msg = await forward_to_db_channel(bot, message)
        quality_data = dict(quality_data)
        quality_data["channel_msg_id"] = channel_msg.id
        await db.add_quality_to_movie(movie_id, quality_key, quality_data)
        logger.info(f"[BG] channel copy done movie={movie_id} q={quality_key} msg={channel_msg.id}")
    except Exception as e:
        logger.error(f"[BG] channel copy failed movie={movie_id} q={quality_key}: {e}")


async def _quick_status_edit(bot: Client, session: dict, text: str, markup=None):
    """Best-effort instant status update (never blocks the save path long)."""
    status_msg_id = session.get("status_msg_id")
    status_chat_id = session.get("status_chat_id")
    if not status_msg_id or not status_chat_id:
        return
    try:
        await bot.edit_message_text(
            chat_id=status_chat_id,
            message_id=status_msg_id,
            text=text,
            reply_markup=markup or _SESSION_MARKUP,
        )
    except Exception:
        pass


async def handle_incoming_movie_file(bot: Client, message: Message):
    try:
        await _handle_incoming_movie_file_inner(bot, message)
    except Exception as e:
        logger.error(f"handle_incoming_movie_file FATAL: {e}", exc_info=True)
        try:
            await message.reply_text(
                f"\u274c <b>File process fail:</b> <code>{type(e).__name__}: {e}</code>",
                quote=True,
            )
        except Exception:
            pass


async def _handle_incoming_movie_file_inner(bot: Client, message: Message):
    if not message.from_user:
        logger.warning("[MEDIA] message.from_user is None \u2013 ignoring")
        return
    admin_id = message.from_user.id
    if admin_id not in Config.ADMINS:
        logger.info(f"[MEDIA] non-admin {admin_id} sent media \u2013 ignoring")
        return

    media = message.video or message.document
    if not media:
        return

    session = await session_mgr.get_session(admin_id)
    if not session:
        await message.reply_text(
            "\u2139\ufe0f **Koi Movie Upload Session Active Nahi Hai.**\n\n"
            "Nayi movie add karne ke liye pehle **/addmovie** command bhejein!",
            quote=True,
        )
        return

    # Instant feedback so admin never feels lag
    filename = getattr(media, "file_name", "") or "Movie_Video"
    filesize = getattr(media, "file_size", 0)
    filesize_str = format_size(filesize)
    await _quick_status_edit(
        bot,
        session,
        f"\u23f3 **Processing\u2026**\n`{filename[:60]}`\n({filesize_str})",
    )

    caption = message.caption or ""
    meta = extract_movie_metadata(filename, caption)
    incoming_title = meta["title"]
    incoming_quality = meta["quality"]
    incoming_year = meta["year"]
    incoming_langs = meta["languages"]

    # Short lock only around session + quality-key assignment (NOT around network)
    async with session_mgr.get_lock(admin_id):
        session = await session_mgr.get_session(admin_id)
        if not session:
            return

        # ==================== CASE 1: FIRST FILE ====================
        if session.get("status") == "AWAITING_FIRST_FILE" or not session.get("title"):
            movie_id = db.generate_movie_id()

            # Poster extraction is relatively fast (small thumb) \u2014 keep it on first file
            extracted_poster, poster_source = await extract_poster_from_message(bot, message)

            movie = await db.create_movie(
                title=incoming_title,
                normalized_title=meta["normalized_title"],
                year=incoming_year,
                languages=incoming_langs,
                poster_file_id=extracted_poster,
                admin_id=admin_id,
                movie_id=movie_id,
            )

            quality_data = {
                "file_id": media.file_id,
                "media_type": "video" if message.video else "document",
                "channel_msg_id": None,  # filled by background copy
                "file_name": filename,
                "file_size": filesize,
                "file_size_formatted": filesize_str,
            }
            await db.add_quality_to_movie(movie_id, incoming_quality, quality_data)
            await session_mgr.register_first_movie_file(
                admin_id=admin_id,
                movie_id=movie_id,
                title=incoming_title,
                year=incoming_year,
                quality=incoming_quality,
                languages=incoming_langs,
                poster_file_id=extracted_poster,
            )

            # Background: permanent copy to DB channel
            asyncio.create_task(
                _bg_copy_and_patch(bot, message, movie_id, incoming_quality, quality_data)
            )

            try:
                await message.delete()
            except Exception:
                pass

            status_text = (
                f"\ud83c\udfac **Movie Session Active**\n\n"
                f"**Title:** `{incoming_title}`\n"
                f"**Year:** `{incoming_year or 'N/A'}`\n"
                f"**Languages:** `{', '.join(incoming_langs) if incoming_langs else 'N/A'}`\n\n"
                f"\u2705 **Added:** `{incoming_quality}` ({filesize_str})\n\n"
                f"Aap agli quality add kar sakte hain ya final link generate kar sakte hain:"
            )
            status_msg_id = session.get("status_msg_id")
            status_chat_id = session.get("status_chat_id", message.chat.id)
            try:
                if status_msg_id:
                    await bot.edit_message_text(
                        chat_id=status_chat_id,
                        message_id=status_msg_id,
                        text=status_text,
                        reply_markup=_SESSION_MARKUP,
                    )
                else:
                    raise Exception("no status msg")
            except Exception:
                new_msg = await message.reply_text(
                    status_text, reply_markup=_SESSION_MARKUP, quote=True
                )
                session = await session_mgr.get_session(admin_id) or session
                session["status_msg_id"] = new_msg.id
                await db.set_active_session(admin_id, session)
            return

        # ==================== CASE 2: ADDITIONAL QUALITY ====================
        active_title = session.get("title", "")
        active_year = session.get("year")
        movie_id = session.get("movie_id")

        is_match, reason, _ = await session_mgr.validate_incoming_upload(
            admin_id, incoming_title
        )

        if not is_match:
            warn = (
                f"\u26a0\ufe0f **Title Mismatch Detected!**\n\n"
                f"Current session: **{active_title}**\n"
                f"This file looks like: **{incoming_title}**\n\n"
                f"\u274c **This file was NOT added.** Finish or cancel the current session before starting another movie."
            )
            await message.reply_text(warn, reply_markup=_SESSION_MARKUP, quote=True)
            return

        existing = (await db.get_movie(movie_id) or {}).get("qualities", {})
        final_quality = incoming_quality
        if incoming_quality in existing:
            idx = 2
            while f"{incoming_quality}_{idx}" in existing:
                idx += 1
            final_quality = f"{incoming_quality}_{idx}"

        quality_data = {
            "file_id": media.file_id,
            "media_type": "video" if message.video else "document",
            "channel_msg_id": None,
            "file_name": filename,
            "file_size": filesize,
            "file_size_formatted": filesize_str,
        }
        await db.add_quality_to_movie(movie_id, final_quality, quality_data)
        await session_mgr.register_additional_quality(admin_id, final_quality)

        # Background permanent copy
        asyncio.create_task(
            _bg_copy_and_patch(bot, message, movie_id, final_quality, quality_data)
        )

        try:
            await message.delete()
        except Exception:
            pass

        qualities_list = (await db.get_movie(movie_id) or {}).get("qualities", {})
        q_str = ", ".join(qualities_list.keys()) if qualities_list else final_quality
        status_text = (
            f"\ud83c\udfac **Movie Session Active**\n\n"
            f"**Title:** `{active_title}`\n"
            f"**Year:** `{active_year or 'N/A'}`\n\n"
            f"\u2705 **Added:** `{final_quality}` ({filesize_str})\n"
            f"**All qualities:** `{q_str}`\n\n"
            f"Aur quality bhejo ya Generate Links dabaao."
        )
        status_msg_id = session.get("status_msg_id")
        status_chat_id = session.get("status_chat_id", message.chat.id)
        try:
            if status_msg_id:
                await bot.edit_message_text(
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text=status_text,
                    reply_markup=_SESSION_MARKUP,
                )
            else:
                raise Exception("no status")
        except Exception:
            new_msg = await message.reply_text(
                status_text, reply_markup=_SESSION_MARKUP, quote=True
            )
            session = await session_mgr.get_session(admin_id) or session
            session["status_msg_id"] = new_msg.id
            await db.set_active_session(admin_id, session)


async def handle_generate_links_action(
    bot: Client, chat_id: int, admin_id: int, query: CallbackQuery = None
):
    """Finishes the session and creates ONE clean, professional final movie card."""
    session = await session_mgr.finish_session(admin_id)
    if not session or not session.get("movie_id"):
        msg = "\u26a0\ufe0f Koi active movie session nahi mila jisme files upload hui hon."
        if query:
            await query.answer(msg, show_alert=True)
        else:
            await bot.send_message(chat_id, msg)
        return

    movie_id = session["movie_id"]
    movie = await db.get_movie(movie_id)
    if not movie or not movie.get("qualities"):
        msg = "\u26a0\ufe0f Is session mein koi quality save nahi hui."
        if query:
            await query.answer(msg, show_alert=True)
        return

    short_link, raw_link = generate_movie_links(movie_id)
    card_text = build_final_movie_card_text(movie)
    card_buttons = build_final_movie_card_buttons(movie)
    poster = movie.get("poster_file_id")

    # If poster is missing, extract on-demand from the saved video in DB_CHANNEL!
    if not poster and movie.get("qualities") and Config.DB_CHANNEL:
        try:
            first_q = list(movie["qualities"].values())[0]
            channel_msg_id = first_q.get("channel_msg_id")
            if channel_msg_id:
                db_msg = await bot.get_messages(Config.DB_CHANNEL, channel_msg_id)
                extracted_poster, _ = await extract_poster_from_message(bot, db_msg)
                if extracted_poster:
                    poster = extracted_poster
                    await db.update_movie_poster(movie_id, poster, "on_demand_extract")
        except Exception as e:
            logger.debug(f"On-demand poster extraction: {e}")

    # 1. Send the final movie card to Admin's private chat
    if poster:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=poster,
                caption=card_text,
                reply_markup=card_buttons,
            )
        except Exception:
            await bot.send_message(chat_id=chat_id, text=card_text, reply_markup=card_buttons)
    else:
        await bot.send_message(chat_id=chat_id, text=card_text, reply_markup=card_buttons)

    # 2. AUTO-POST DIRECTLY TO MOVIE GROUP!
    group_posted = False
    if Config.MOVIE_GROUP_ID:
        try:
            group_buttons = build_final_movie_card_buttons(movie, for_group=True)
            group_post = None
            if poster:
                group_post = await bot.send_photo(
                    chat_id=Config.MOVIE_GROUP_ID,
                    photo=poster,
                    caption=card_text,
                    reply_markup=group_buttons,
                )
            else:
                group_post = await bot.send_message(
                    chat_id=Config.MOVIE_GROUP_ID,
                    text=card_text,
                    reply_markup=group_buttons,
                )
            group_posted = True
            if group_post:
                await db.update_movie_group_post(
                    movie_id, Config.MOVIE_GROUP_ID, group_post.id
                )
            logger.info(
                f"Successfully posted movie '{movie.get('title')}' to Movie Group: {Config.MOVIE_GROUP_ID}"
            )
        except Exception as e:
            logger.warning(
                f"Could not auto-post to Movie Group {Config.MOVIE_GROUP_ID}: {e}"
            )

    # 3. Send success message with shareable deep link
    qualities_str = ", ".join(movie.get("qualities", {}).keys())
    group_note = (
        "\n\ud83d\udce2 **Group Status:** \u2705 Seedha group me upload ho gaya!"
        if group_posted
        else ""
    )
    success_text = (
        f"\u2705 **Movie Upload Complete!**\n\n"
        f"\ud83c\udfac **Movie:** `{movie.get('title')}`\n"
        f"\ud83d\udcc1 **Saved Qualities:** `[{qualities_str}]`\n"
        f"\ud83c\udd94 **Movie ID:** `{movie_id}`{group_note}\n\n"
        f"\ud83d\udd17 **Shareable Link:**\n`{short_link}`\n\n"
        f"\u2728 Final movie card aapko yahan aur group mein upload kar diya gaya hai!"
    )
    admin_markup = None
    if Config.MOVIE_GROUP_LINK:
        admin_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("\ud83d\udce2 View Post in Group", url=Config.MOVIE_GROUP_LINK)]]
        )
    await bot.send_message(chat_id=chat_id, text=success_text, reply_markup=admin_markup)

    if query:
        await query.answer("Movie Card & Links Generated & Posted to Group!")
