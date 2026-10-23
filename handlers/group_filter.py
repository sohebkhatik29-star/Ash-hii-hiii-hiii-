# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/group_filter.py
# Purpose: Universal Movie Search & Group AutoFilter (Rose / AutoFilter style).
#          Works in Groups, Supergroups, and Private Chat.
#          When user types movie name → shows group post links (jump to existing post).
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

# Common noise words – ignore these so chat stays clean
IGNORED_WORDS = {
    "hi", "hello", "hey", "hlo", "hii", "hiii", "ok", "okay", "bye", "gm", "gn",
    "ha", "haa", "nahi", "no", "yes", "haan", "admin", "thanks", "thank you",
    "thx", "tq", "good", "nice", "please", "plz", "pls", "link", "bot", "movie",
    "film", "bhai", "bro", "sir", "ji", "kya", "hai", "ho", "ka", "ki", "ke",
    "aur", "or", "the", "a", "an", "to", "of", "in", "on", "for"
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
    Builds a clean list like:
        1. Pushpa 1 (2021)
           🔗 Link
        2. Pushpa 2 (2024)
           🔗 Link
    with clickable buttons that jump straight to the group post.
    """
    total = len(results)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total)
    page_results = results[start_idx:end_idx]

    text = f"🎬 <b>Results for:</b> <code>{query}</code>\n"
    text += f"📄 <b>Page {page}/{total_pages}</b> • Total: <b>{total}</b>\n\n"

    buttons = []

    for i, m in enumerate(page_results, start=start_idx + 1):
        title = m.get("title", "Untitled")
        year = m.get("year")
        year_str = f" ({year})" if year else ""
        languages = m.get("languages") or []
        lang_str = " | ".join(languages) if languages else ""

        post_link = _get_post_link(m)

        # Text style exactly as requested
        text += f"<b>{i}. {title}{year_str}</b>"
        if lang_str:
            text += f"  [{lang_str}]"
        text += f"\n🔗 <a href=\"{post_link}\">Link</a>\n\n"

        # Button that opens the existing group post
        btn_label = f"🎬 {title[:28]}{year_str}"
        buttons.append([InlineKeyboardButton(btn_label, url=post_link)])

    # Pagination
    nav_row = []
    # Keep query short in callback (Telegram limit 64 bytes)
    safe_q = re.sub(r"[^\w\s]", "", query)[:30].strip() or "q"
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"flt_{page-1}_{safe_q}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"flt_{page+1}_{safe_q}"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🤖 Open Movie Bot", url=f"https://t.me/{Config.BOT_USERNAME}"),
        InlineKeyboardButton("❌ Close", callback_data="close_card")
    ])

    return text, InlineKeyboardMarkup(buttons)


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

    # 2. Direct text search (auto-filter style) – works only when Group Privacy is OFF
    @bot.on_message(
        filters.text
        & ~filters.command(["start", "help", "find", "filter", "search", "addmovie",
                            "newmovie", "finish", "cancel", "status", "movies",
                            "delete", "stats", "broadcast", "generatelink"])
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
                        f"🔍 Spelling check karein ya thoda short name try karein.",
                        quote=True
                    )
                    if is_group:
                        await asyncio.sleep(25)
                        try:
                            await not_found.delete()
                        except Exception:
                            pass
                return

            # Private chat + single exact match → full card directly
            if not is_group and len(results) == 1:
                user_id = message.from_user.id if message.from_user else 0
                await send_movie_card(client, user_id=user_id, movie_id=results[0]["movie_id"], is_admin=False)
                return

            result_text, result_markup = build_filter_results(query, results, page=1, is_group=is_group)
            await message.reply_text(
                text=result_text,
                reply_markup=result_markup,
                disable_web_page_preview=True,
                quote=True
            )
        except Exception as e:
            logger.error(f"Error in execute_movie_search: {e}")

    # 3. Pagination callbacks
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

    # 4. Close button
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
