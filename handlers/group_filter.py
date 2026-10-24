# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/group_filter.py
# Purpose: Universal Movie Search & Group AutoFilter (Rose / AutoFilter style).
#          Works in Groups, Supergroups, and Private Chat.
#          When user types movie name → shows group post links (jump to existing post).
#          Auto-deletes search messages after 24h. /done marks fulfilled requests.
# ==============================================================================

import asyncio
import logging
import re
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from configs import Config
from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.send_file import send_movie_card

logger = logging.getLogger("GroupFilter")

PAGE_SIZE = 8
# Group search messages auto-delete after 24 hours
AUTO_DELETE_SECONDS = 24 * 60 * 60  # 86400

# Common noise words – ignore these so chat stays clean
IGNORED_WORDS = {
    "hi", "hello", "hey", "hlo", "hii", "hiii", "ok", "okay", "bye", "gm", "gn",
    "ha", "haa", "nahi", "no", "yes", "haan", "admin", "thanks", "thank you",
    "thx", "tq", "good", "nice", "please", "plz", "pls", "link", "bot", "movie",
    "film", "bhai", "bro", "sir", "ji", "kya", "hai", "ho", "ka", "ki", "ke",
    "aur", "or", "the", "a", "an", "to", "of", "in", "on", "for", "done"
}


def _get_post_link(m: dict) -> str:
    """Return direct group post link if available, else bot deep-link fallback."""
    post_link = m.get("group_post_link")
    if post_link:
        return post_link

    group_id = m.get("group_chat_id") or Config.MOVIE_GROUP_ID
    msg_id = m.get("group_msg_id")
    if msg_id and group_id:
        return db.make_telegram_post_link(group_id, msg_id)

    # Fallback: open movie card inside bot
    return f"https://t.me/{Config.BOT_USERNAME}?start=mov_{m.get('movie_id')}"


def build_filter_results(query: str, results: list, page: int = 1, is_group: bool = True):
    """
    Clean text-only results (no buttons):
        1. Buddy (2026)  [English]
        🔗 Link https://t.me/c/xxxxx/yyyyy
    """
    total = len(results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total)
    page_results = results[start_idx:end_idx]

    text = f"🎬 <b>Results for:</b> <code>{query}</code>\n"
    text += f"📄 <b>Page {page}/{total_pages}</b> • Total: <b>{total}</b>\n\n"

    for i, m in enumerate(page_results, start=start_idx + 1):
        title = m.get("title", "Untitled")
        year = m.get("year")
        year_str = f" ({year})" if year else ""
        languages = m.get("languages") or []
        lang_str = " | ".join(languages) if languages else ""

        post_link = _get_post_link(m)

        text += f"<b>{i}. {title}{year_str}</b>"
        if lang_str:
            text += f"  [{lang_str}]"
        # Full visible link (Telegram auto-makes it clickable)
        text += f"\n🔗 Link {post_link}\n\n"

    # No buttons — only text links as requested
    return text, None


async def _safe_delete(client: Client, chat_id: int, message_ids: list):
    """Delete messages; ignore errors (already deleted / no permission)."""
    for mid in message_ids:
        if not mid:
            continue
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass


async def _schedule_delete(client: Client, chat_id: int, message_ids: list, delay: int = AUTO_DELETE_SECONDS):
    """Background task: wait then delete the given messages."""
    try:
        await asyncio.sleep(delay)
        await _safe_delete(client, chat_id, message_ids)
    except Exception as e:
        logger.debug(f"schedule_delete: {e}")


def register_group_filter_handlers(bot: Client):

    # 1. Explicit commands: /find /filter /search
    @bot.on_message(filters.command(["find", "filter", "search"]))
    async def filter_command_handler(client: Client, message: Message):
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            await message.reply_text(
                "💡 **Usage:** `/find <movie name>`\nExample: `/find Pushpa`",
                quote=True
            )
            return

        query = parts[1].strip()
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        await execute_movie_search(client, message, query, is_group=is_group)

    # 2. /done — admin replies to a user's movie request after uploading
    @bot.on_message(filters.command("done") & filters.group)
    async def done_request_handler(client: Client, message: Message):
        """
        Admin: reply to user's movie-name message with /done
        → delete /done command
        → reply to user: request uploaded
        → auto-delete user msg + bot reply after 24h
        """
        admin_id = message.from_user.id if message.from_user else 0
        if admin_id not in Config.ADMINS:
            try:
                await message.delete()
            except Exception:
                pass
            return

        if not message.reply_to_message:
            tip = await message.reply_text(
                "⚠️ <b>/done</b> ko user ke request message pe <b>reply</b> karke bhejo.",
                quote=True
            )
            await asyncio.sleep(15)
            await _safe_delete(client, message.chat.id, [message.id, tip.id])
            return

        user_req = message.reply_to_message

        # Delete admin's /done command immediately
        try:
            await message.delete()
        except Exception:
            pass

        # Reply to the original user request
        confirm = await user_req.reply_text(
            "✅ <b>Your request has been uploaded.</b>\n"
            "Please check it in the group / search the movie name again.",
            quote=True
        )

        # Schedule 24h delete: user's original request + bot confirmation
        asyncio.create_task(
            _schedule_delete(
                client,
                message.chat.id,
                [user_req.id, confirm.id],
                AUTO_DELETE_SECONDS
            )
        )

    # 3. Direct text search (auto-filter style) – works only when Group Privacy is OFF
    @bot.on_message(
        filters.text
        & ~filters.command(["start", "help", "find", "filter", "search", "addmovie",
                            "newmovie", "finish", "cancel", "status", "movies",
                            "delete", "stats", "broadcast", "generatelink", "done"])
        & ~filters.via_bot
    )
    async def direct_text_search_handler(client: Client, message: Message):
        # Ignore bots & channel posts
        if message.from_user and message.from_user.is_bot:
            return
        if message.sender_chat and not message.from_user:
            # anonymous admin / channel – skip to avoid loops
            return

        user_id = message.from_user.id if message.from_user else 0
        raw_text = (message.text or "").strip()

        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)

        # Private chat: don't interfere with active /addmovie session
        if not is_group:
            session = await session_mgr.get_session(user_id)
            if session and session.get("status") in ("AWAITING_FIRST_FILE", "ACTIVE"):
                return

        # Noise filter
        lower = raw_text.lower()
        if len(raw_text) < 2 or lower in IGNORED_WORDS:
            return
        # Ignore pure numbers / single short tokens that look like chat
        if len(raw_text) <= 3 and not any(c.isalpha() for c in raw_text):
            return

        await execute_movie_search(client, message, raw_text, is_group=is_group)

    async def execute_movie_search(client: Client, message: Message, query: str, is_group: bool = True):
        try:
            results = await db.search_movies(query)

            if not results:
                if len(query) >= 3:
                    not_found = await message.reply_text(
                        f"❌ <b>'{query}'</b> group / database mein nahi mila!\n\n"
                        f"🔍 Spelling check karein ya thoda short name try karein.\n"
                        f"⏳ Request save hai — upload hone ke baad admin /done se notify karega.",
                        quote=True
                    )
                    # Only auto-delete the bot's not-found reply after 24h.
                    # Keep user's request message so admin can /done reply on it.
                    if is_group:
                        asyncio.create_task(
                            _schedule_delete(
                                client,
                                message.chat.id,
                                [not_found.id],
                                AUTO_DELETE_SECONDS
                            )
                        )
                return

            # Private chat + single exact match → full card directly
            if not is_group and len(results) == 1:
                user_id = message.from_user.id if message.from_user else 0
                await send_movie_card(client, user_id=user_id, movie_id=results[0]["movie_id"], is_admin=False)
                return

            result_text, result_markup = build_filter_results(query, results, page=1, is_group=is_group)
            bot_msg = await message.reply_text(
                text=result_text,
                reply_markup=result_markup,
                disable_web_page_preview=True,
                quote=True
            )

            # Group: auto-delete user search + bot results after 24h
            if is_group:
                asyncio.create_task(
                    _schedule_delete(
                        client,
                        message.chat.id,
                        [message.id, bot_msg.id],
                        AUTO_DELETE_SECONDS
                    )
                )
        except Exception as e:
            logger.error(f"Error in execute_movie_search: {e}")

    # 4. Pagination callbacks (kept for compatibility; no buttons now)
    @bot.on_callback_query(filters.regex(r"^flt_(\d+)_(.+)"))
    async def filter_pagination_callback(client: Client, query: CallbackQuery):
        match = query.matches[0]
        page = int(match.group(1))
        search_query = match.group(2)

        results = await db.search_movies(search_query)
        if not results:
            await query.answer("Results expire ho gaye / nahi mile!", show_alert=True)
            return

        is_group = query.message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        result_text, result_markup = build_filter_results(search_query, results, page=page, is_group=is_group)
        try:
            await query.message.edit_text(
                text=result_text,
                reply_markup=result_markup,
                disable_web_page_preview=True
            )
            await query.answer()
        except Exception as e:
            logger.debug(f"Pagination edit: {e}")
            await query.answer()

    # 5. Close button
    @bot.on_callback_query(filters.regex(r"^close_card$"))
    async def close_card_callback(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_text("❌ Closed")
            except Exception:
                pass
        await query.answer()
