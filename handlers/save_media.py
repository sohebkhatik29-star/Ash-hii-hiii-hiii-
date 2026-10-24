# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/save_media.py
# Purpose: Handles incoming media strictly within the /addmovie session,
#          maintains a single edited status message, prevents KGF in Jaat,
#          and eliminates chat clutter.
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

async def handle_incoming_movie_file(bot: Client, message: Message):
    admin_id = message.from_user.id
    if admin_id not in Config.ADMINS:
        return

    media = message.video or message.document
    if not media:
        return

    # Check if there is an active /addmovie session
    session = await session_mgr.get_session(admin_id)
    if not session:
        await message.reply_text(
            "ℹ️ **Koi Movie Upload Session Active Nahi Hai.**\n\n"
            "Nayi movie add karne ke liye pehle **/addmovie** command bhejein!",
            quote=True
        )
        return

    # Use per-admin lock to process forwarded files sequentially without race conditions
    async with session_mgr.get_lock(admin_id):
        session = await session_mgr.get_session(admin_id)
        if not session:
            return

        filename = getattr(media, "file_name", "") or "Movie_Video"
        caption = message.caption or ""
        filesize = getattr(media, "file_size", 0)
        filesize_str = format_size(filesize)

        meta = extract_movie_metadata(filename, caption)
        incoming_title = meta["title"]
        incoming_quality = meta["quality"]
        incoming_year = meta["year"]
        incoming_langs = meta["languages"]

        # ==================== CASE 1: FIRST FILE IN SESSION ====================
        if session.get("status") == "AWAITING_FIRST_FILE" or not session.get("title"):
            movie_id = db.generate_movie_id()
            extracted_poster, poster_source = await extract_poster_from_message(bot, message)

            movie = await db.create_movie(
                title=incoming_title,
                normalized_title=meta["normalized_title"],
                year=incoming_year,
                languages=incoming_langs,
                poster_file_id=extracted_poster,
                admin_id=admin_id,
                movie_id=movie_id
            )

            channel_msg = await forward_to_db_channel(bot, message)
            quality_data = {
                "file_id": media.file_id,
                "media_type": "video" if message.video else "document",
                "channel_msg_id": channel_msg.id,
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
                languages=incoming_langs
            )

            # Try to delete the uploaded file message to keep chat clean
            try:
                await message.delete()
            except Exception:
                pass

            status_text = (
                f"🎬 **Movie Session Active**\n\n"
                f"**Title:** `{incoming_title}`\n"
                f"**Year:** `{incoming_year or 'N/A'}`\n"
                f"**Languages:** `{', '.join(incoming_langs) if incoming_langs else 'N/A'}`\n\n"
                f"✅ **Added:** `{incoming_quality}` ({filesize_str})\n\n"
                f"Aap agli quality add kar sakte hain ya final link generate kar sakte hain:"
            )
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Continue Upload", callback_data="session_continue"),
                    InlineKeyboardButton("🔗 Generate Links", callback_data="session_generate")
                ],
                [
                    InlineKeyboardButton("❌ Cancel Session", callback_data="session_cancel")
                ]
            ])

            status_msg_id = session.get("status_msg_id")
            status_chat_id = session.get("status_chat_id", message.chat.id)
            try:
                if status_msg_id:
                    await bot.edit_message_text(
                        chat_id=status_chat_id,
                        message_id=status_msg_id,
                        text=status_text,
                        reply_markup=markup
                    )
                else:
                    raise Exception("no status msg")
            except Exception:
                new_msg = await message.reply_text(status_text, reply_markup=markup, quote=True)
                session = await session_mgr.get_session(admin_id) or session
                session["status_msg_id"] = new_msg.id
                await db.set_active_session(admin_id, session)
            return

        # ==================== CASE 2: ADDITIONAL QUALITY ====================
        active_title = session.get("title", "")
        active_year = session.get("year")
        movie_id = session.get("movie_id")

        is_match, reason, _ = await session_mgr.validate_incoming_upload(admin_id, incoming_title)

        if not is_match:
            warn = (
                f"⚠️ **Title Mismatch Detected!**\n\n"
                f"Current session: **{active_title}**\n"
                f"This file looks like: **{incoming_title}**\n\n"
                f"❌ **This file was NOT added.** Finish or cancel the current session before starting another movie."
            )
            markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("➕ Continue Upload", callback_data="session_continue"),
                    InlineKeyboardButton("🔗 Generate Links", callback_data="session_generate")
                ],
                [
                    InlineKeyboardButton("❌ Cancel Session", callback_data="session_cancel")
                ]
            ])
            await message.reply_text(warn, reply_markup=markup, quote=True)
            return

        # Forward + save quality
        channel_msg = await forward_to_db_channel(bot, message)
        quality_data = {
            "file_id": media.file_id,
            "media_type": "video" if message.video else "document",
            "channel_msg_id": channel_msg.id,
            "file_name": filename,
            "file_size": filesize,
            "file_size_formatted": filesize_str,
        }

        # Handle duplicate quality key (e.g. two 480p)
        existing = (await db.get_movie(movie_id) or {}).get("qualities", {})
        final_quality = incoming_quality
        if incoming_quality in existing:
            # Rename with size or index
            idx = 2
            while f"{incoming_quality}_{idx}" in existing:
                idx += 1
            old_key_labeled = f"{incoming_quality}_{idx}"
            final_quality = old_key_labeled
            try:
                await session_mgr.rename_session_quality(admin_id, incoming_quality, old_key_labeled)
            except Exception:
                pass

        await db.add_quality_to_movie(movie_id, final_quality, quality_data)
        await session_mgr.register_additional_quality(admin_id, final_quality)

        try:
            await message.delete()
        except Exception:
            pass

        movie = await db.get_movie(movie_id) or {}
        qualities_list = list(movie.get("qualities", {}).keys())
        qualities_str = ", ".join(qualities_list)

        status_text = (
            f"🎬 **Movie Session Active**\n\n"
            f"**Title:** `{active_title}`\n"
            f"**Year:** `{active_year or 'N/A'}`\n\n"
            f"✅ **Qualities so far:** `[{qualities_str}]`\n"
            f"➕ **Just added:** `{final_quality}` ({filesize_str})\n\n"
            f"Aap agli quality add kar sakte hain ya final link generate kar sakte hain:"
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Continue Upload", callback_data="session_continue"),
                InlineKeyboardButton("🔗 Generate Links", callback_data="session_generate")
            ],
            [
                InlineKeyboardButton("❌ Cancel Session", callback_data="session_cancel")
            ]
        ])

        status_msg_id = session.get("status_msg_id")
        status_chat_id = session.get("status_chat_id", message.chat.id)
        try:
            if status_msg_id:
                await bot.edit_message_text(
                    chat_id=status_chat_id,
                    message_id=status_msg_id,
                    text=status_text,
                    reply_markup=markup
                )
            else:
                raise Exception("no status msg")
        except Exception:
            new_msg = await message.reply_text(status_text, reply_markup=markup, quote=True)
            session = await session_mgr.get_session(admin_id) or session
            session["status_msg_id"] = new_msg.id
            await db.set_active_session(admin_id, session)


async def handle_generate_links_action(bot: Client, chat_id: int, admin_id: int, query: CallbackQuery = None):
    """Finishes the session and creates ONE clean, professional final movie card."""
    session = await session_mgr.finish_session(admin_id)
    if not session or not session.get("movie_id"):
        msg = "⚠️ Koi active movie session nahi mila jisme files upload hui hon."
        if query:
            await query.answer(msg, show_alert=True)
        else:
            await bot.send_message(chat_id, msg)
        return

    movie_id = session["movie_id"]
    movie = await db.get_movie(movie_id)
    if not movie or not movie.get("qualities"):
        msg = "⚠️ Is session mein koi quality save nahi hui."
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
                reply_markup=card_buttons
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
                    reply_markup=group_buttons
                )
            else:
                group_post = await bot.send_message(
                    chat_id=Config.MOVIE_GROUP_ID,
                    text=card_text,
                    reply_markup=group_buttons
                )
            group_posted = True
            if group_post:
                await db.update_movie_group_post(movie_id, Config.MOVIE_GROUP_ID, group_post.id)
            logger.info(f"Successfully posted movie '{movie.get('title')}' to Movie Group: {Config.MOVIE_GROUP_ID}")
        except Exception as e:
            logger.warning(f"Could not auto-post to Movie Group {Config.MOVIE_GROUP_ID}: {e}")

    # 3. Send success message with shareable deep link
    qualities_str = ", ".join(movie.get("qualities", {}).keys())
    group_note = "\n📢 **Group Status:** ✅ Seedha group me upload ho gaya!" if group_posted else ""
    success_text = (
        f"✅ **Movie Upload Complete!**\n\n"
        f"🎬 **Movie:** `{movie.get('title')}`\n"
        f"📁 **Saved Qualities:** `[{qualities_str}]`\n"
        f"🆔 **Movie ID:** `{movie_id}`{group_note}\n\n"
        f"🔗 **Shareable Link:**\n`{short_link}`\n\n"
        f"✨ Final movie card aapko yahan aur group mein upload kar diya gaya hai!"
    )
    admin_markup = None
    if Config.MOVIE_GROUP_LINK:
        admin_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 View Post in Group", url=Config.MOVIE_GROUP_LINK)]
        ])
    await bot.send_message(chat_id=chat_id, text=success_text, reply_markup=admin_markup)

    if query:
        await query.answer("Movie Card & Links Generated & Posted to Group!")
