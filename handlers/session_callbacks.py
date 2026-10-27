# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/session_callbacks.py
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import QueryIdInvalid

from configs import Config
from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.save_media import handle_generate_links_action
from handlers.send_file import deliver_movie_quality, send_movie_card, send_media_and_reply

logger = logging.getLogger(__name__)

def register_callback_handlers(bot: Client):

    @bot.on_callback_query()
    async def callback_router(client: Client, query: CallbackQuery):
        data = query.data or ""
        user_id = query.from_user.id if query.from_user else 0

        # Admin Panel callbacks (ap_*) — MUST handle here because this is a catch-all
        if data.startswith("ap_"):
            try:
                from handlers.admin_panel import handle_ap_callback
                await handle_ap_callback(client, query)
            except Exception as e:
                logger.error(f"ap callback: {e}", exc_info=True)
                try:
                    await query.answer(f"Panel error: {type(e).__name__}", show_alert=True)
                except Exception:
                    pass
            return

        # Force-sub buttons — handle BEFORE anything else
        if data.startswith("fs_mode:") or data.startswith("fs_refresh") or data == "fs_noop":
            try:
                from handlers.force_sub_handler import handle_fs_callback
                await handle_fs_callback(client, query)
            except Exception as e:
                logger.error(f"fs callback: {e}", exp_info=True)
                try:
                    await query.answer(f"Error: {e}", show_alert=True)
                except Exception:
                    pass
            return

        # 0. Direct Channel Msg ID Download: f_{channel_msg_id}
        if data.startswith("f_"):
            try:
                msg_id = int(data.split("_", 1)[-1])
                await query.answer("Delivering file... please wait!")
                await send_media_and_reply(client, user_id=user_id, file_id=msg_id)
                return
            except Exception as e:
                logger.error(f"Error delivering f_ callback: {e}")
                await query.answer("Delivery failed, please retry!", show_alert=True)
                return

        # 1. Quality Download: dl_{movie_id}_{quality}
        elif data.startswith("dl_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                movie_id = parts[1]
                quality = parts[2]
                await deliver_movie_quality(client, query, movie_id, quality)
                return

        # 2. Preview Movie Card: preview_mov_{movie_id}
        elif data.startswith("preview_mov_"):
            movie_id = data.split("preview_mov_", 1)[-1]
            is_admin = user_id in Config.ADMINS
            await send_movie_card(client, user_id=user_id, movie_id=movie_id, is_admin=is_admin)
            await query.answer()
            return

        # 3. Session Continue Upload Button
        elif data == "session_continue":
            if user_id not in Config.ADMINS:
                return
            session = await session_mgr.get_session(user_id)
            if not session or not session.get("title"):
                await query.answer("Send the first video file now!", show_alert=True)
                return
            session_mgr.set_awaiting_next(user_id, True)
            title = session.get("title")
            year_str = f" ({session.get('year')})" if session.get("year") else ""
            qualities_str = ", ".join(session.get("qualities", [])) or "None"
            text = (
                f"🎬 <b>Movie: {title}{year_str}</b>\n\n"
                f"➕ <b>Send the next quality file now</b> (e.g. 720p, 1080p, 4K).\n\n"
                f"📁 Already saved: <code>[{qualities_str}]</code>"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Generate Links", callback_data="session_generate")],
                [InlineKeyboardButton("❌ Cancel Session", callback_data="session_cancel")]
            ])
            try:
                await query.message.edit_text(text, reply_markup=markup)
            except Exception:
                pass
            await query.answer()
            return

        # 4. Session Generate Links
        elif data == "session_generate":
            if user_id not in Config.ADMINS:
                return
            await query.answer("Generating links...")
            await handle_generate_links_action(client, query.message.chat.id, user_id)
            return

        # 5. Session Cancel
        elif data == "session_cancel":
            if user_id not in Config.ADMINS:
                return
            await session_mgr.cancel_session(user_id)
            try:
                await query.message.edit_text("❌ <b>Movie upload session cancelled.</b>")
            except Exception:
                pass
            await query.answer("Session cancelled")
            return

        # 6. About bot / dev / close / back
        elif data == "about_bot":
            try:
                await query.message.edit_text(
                    Config.ABOUT_BOT_TEXT,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="back_home")]
                    ])
                )
            except Exception:
                pass
            await query.answer()

        elif data == "about_dev":
            try:
                await query.message.edit_text(
                    Config.ABOUT_DEV_TEXT,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="back_home")]
                    ])
                )
            except Exception:
                pass
            await query.answer()

        elif data == "back_home":
            safe_name = (query.from_user.first_name or "Movie Lover").replace("[", "").replace("]", "")
            welcome_text = Config.HOME_TEXT.format(
                brand=Config.BRAND_NAME,
                first_name=safe_name,
                user_id=user_id
            )
            channel_url = f"https://t.me/{Config.UPDATES_CHANNEL}" if Config.UPDATES_CHANNEL and not str(Config.UPDATES_CHANNEL).startswith("-") else f"https://t.me/{Config.BOT_USERNAME}"
            rows = [
                [
                    InlineKeyboardButton("📢 Updates Channel", url=channel_url),
                    InlineKeyboardButton("🍿 Dev Info", callback_data="about_dev")
                ],
                [
                    InlineKeyboardButton("ℹ️ About Bot", callback_data="about_bot"),
                    InlineKeyboardButton("❌ Close", callback_data="close_card")
                ]
            ]
            if user_id in (Config.ADMINS or []):
                rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="ap_home")])
            markup = InlineKeyboardMarkup(rows)
            try:
                await query.message.edit(welcome_text, reply_markup=markup, disable_web_page_preview=True)
            except Exception:
                pass
            await query.answer()

        elif data == "close_card":
            try:
                await query.message.delete()
            except Exception:
                pass

        try:
            await query.answer()
        except QueryIdInvalid:
            pass
