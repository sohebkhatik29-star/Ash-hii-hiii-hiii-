# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/start_handler.py
# Purpose: Dedicated handler for /start, deep links, welcome menus, and informational cards.
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import QueryIdInvalid

from configs import Config
from handlers.add_user_to_db import add_user_to_database
from handlers.force_sub_handler import handle_force_sub
from handlers.helpers import b64_to_str
from handlers.send_file import send_movie_card, send_media_and_reply, deliver_movie_quality_direct

logger = logging.getLogger(__name__)

def register_start_handlers(bot: Client):

    @bot.on_message(filters.command("start") & filters.private)
    async def start_cmd(client: Client, cmd: Message):
        user_id = cmd.from_user.id
        if user_id in Config.BANNED_USERS:
            return

        # Multi-channel force-sub (Admin/Owner exempt) — group + bot
        if user_id not in (Config.ADMINS or []):
            status = await handle_force_sub(client, cmd)
            if status == 400:
                return

        text = cmd.text.strip()
        parts = text.split(" ", 1)

        # 1. Standard Start Command (No Arguments)
        if len(parts) == 1 or parts[1] == "/start":
            await add_user_to_database(client, cmd)
            safe_name = (cmd.from_user.first_name or "Movie Lover").replace("[", "").replace("]", "")
            welcome_text = Config.HOME_TEXT.format(
                brand=Config.BRAND_NAME,
                first_name=safe_name,
                user_id=user_id
            )
            channel_url = "https://t.me/ash_movie_1782"
            rows = [
                [
                    InlineKeyboardButton("📢 Updates Channel", url=channel_url),
                    InlineKeyboardButton("🍿 Dev Info", callback_data="about_dev")
                ],
                [
                    InlineKeyboardButton("ℹ️ About Bot", callback_data="about_bot"),
                    InlineKeyboardButton("❌ Close", callback_data="close_card")
                ],
            ]
            if user_id in (Config.ADMINS or []):
                rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="ap_home")])
            markup = InlineKeyboardMarkup(rows)
            await cmd.reply_text(welcome_text, reply_markup=markup, quote=True, disable_web_page_preview=True)
            return

        # 2. Deep Link Handling
        payload = parts[1].strip()
        try:
            # Movie card: m_<id>
            if payload.startswith("m_"):
                movie_id = payload[2:]
                is_admin = user_id in (Config.ADMINS or [])
                await send_movie_card(client, user_id=user_id, movie_id=movie_id, is_admin=is_admin)
                return

            # Direct file: f_<msg_id>
            if payload.startswith("f_"):
                try:
                    msg_id = int(payload[2:])
                    await send_media_and_reply(client, user_id=user_id, file_id=msg_id)
                except Exception as e:
                    logger.error(f"deep f_ error: {e}")
                    await cmd.reply_text("File deliver fail. Retry later.")
                return

            # Quality: q_<movie_id>_<quality>
            if payload.startswith("q_"):
                rest = payload[2:]
                if "_" in rest:
                    mid, quality = rest.rsplit("_", 1)
                    await deliver_movie_quality_direct(client, cmd, mid, quality)
                return

            # Base64 legacy
            try:
                decoded = b64_to_str(payload)
                if decoded:
                    if decoded.startswith("m_"):
                        movie_id = decoded[2:]
                        is_admin = user_id in (Config.ADMINS or [])
                        await send_movie_card(client, user_id=user_id, movie_id=movie_id, is_admin=is_admin)
                        return
            except Exception:
                pass

            await cmd.reply_text("Invalid link. /start dabao.")
        except Exception as e:
            logger.exception("start deep link: %s", e)
            await cmd.reply_text("Error. /start se dubara try karo.")

    @bot.on_callback_query(filters.regex(r"^(about_bot|about_dev)$"))
    async def about_cb(client: Client, query: CallbackQuery):
        data = query.data or ""
        try:
            if data == "about_bot":
                await query.message.edit_text(
                    Config.ABOUT_BOT_TEXT,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="start_home")],
                        [InlineKeyboardButton("❌ Close", callback_data="close_card")],
                    ]),
                    disable_web_page_preview=True,
                )
            elif data == "about_dev":
                await query.message.edit_text(
                    Config.ABOUT_DEV_TEXT,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Back", callback_data="start_home")],
                        [InlineKeyboardButton("❌ Close", callback_data="close_card")],
                    ]),
                    disable_web_page_preview=True,
                )
            await query.answer()
        except QueryIdInvalid:
            pass
        except Exception as e:
            logger.warning("start callback: %s", e)

    @bot.on_callback_query(filters.regex(r"^start_home$"))
    async def start_home_cb(client: Client, query: CallbackQuery):
        user = query.from_user
        if not user:
            return
        user_id = user.id
        safe_name = (user.first_name or "Movie Lover").replace("[", "").replace("]", "")
        welcome_text = Config.HOME_TEXT.format(
            brand=Config.BRAND_NAME,
            first_name=safe_name,
            user_id=user_id,
        )
        channel_url = "https://t.me/ash_movie_1782"
        rows = [
            [
                InlineKeyboardButton("📢 Updates Channel", url=channel_url),
                InlineKeyboardButton("🍿 Dev Info", callback_data="about_dev"),
            ],
            [
                InlineKeyboardButton("ℹ️ About Bot", callback_data="about_bot"),
                InlineKeyboardButton("❌ Close", callback_data="close_card"),
            ],
        ]
        if user_id in (Config.ADMINS or []):
            rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="ap_home")])
        try:
            await query.message.edit_text(
                welcome_text,
                reply_markup=InlineKeyboardMarkup(rows),
                disable_web_page_preview=True,
            )
            await query.answer()
        except Exception as e:
            logger.warning("start_home: %s", e)
