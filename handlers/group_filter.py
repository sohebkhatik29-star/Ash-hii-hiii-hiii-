# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/group_filter.py
# Purpose: Universal Movie Search & Group AutoFilter (Rose / AutoFilter style).
#          Works in Groups, Supergroups, and Private Chat.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from configs import Config
from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.send_file import send_movie_card

logger = logging.getLogger("GroupFilter")

PAGE_SIZE = 5

IGNORED_WORDS = {
    "hi", "hello", "hey", "hlo", "ok", "okay", "bye", "gm", "gn", "ha", "haa",
    "nahi", "admin", "thanks", "thank you", "thx", "tq", "good", "nice",
    "please", "plz", "pls", "link", "bot", "movie", "film"
}

def build_filter_results(query: str, results: list, page: int = 1, is_group: bool = True):
    total = len(results)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total)
    page_results = results[start_idx:end_idx]

    text = f"🎬 <b>Results for:</b> <code>{query}</code>\n"
    text += f"📄 <b>Page {page}/{total_pages} • Total: {total}</b>\n\n"

    buttons = []

    for i, m in enumerate(page_results, start=start_idx + 1):
        title = m.get("title", "Untitled")
        year = m.get("year")
        year_str = f" ({year})" if year else ""
        languages = m.get("languages", ["Hindi"])
        lang_str = " | ".join(languages) if languages else "Hindi"

        # Determine direct post link in group
        post_link = m.get("group_post_link")
        if not post_link:
            group_id = m.get("group_chat_id") or Config.MOVIE_GROUP_ID
            msg_id = m.get("group_msg_id")
            if msg_id and group_id:
                post_link = db.make_telegram_post_link(group_id, msg_id)
            else:
                post_link = f"https://t.me/{Config.BOT_USERNAME}?start=mov_{m.get('movie_id')}"

        text += f"<b>{i}. {title}{year_str}</b> [{lang_str}]\n"
        text += f"🔗 <b>Link:</b> {post_link}\n\n"

        # Quick-jump button
        button_label = f"🎬 {i}. {title[:20]}{year_str}"
        buttons.append([InlineKeyboardButton(button_label, url=post_link)])

    # Pagination buttons
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"flt_{page-1}_{query[:20]}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"flt_{page+1}_{query[:20]}"))

    if nav_row:
        buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton("🤖 Open Movie Bot", url=f"https://t.me/{Config.BOT_USERNAME}"),
        InlineKeyboardButton("❌ Close", callback_data="close_card")
    ])

    return text, InlineKeyboardMarkup(buttons)

def register_group_filter_handlers(bot: Client):

    # 1. Search via Command in Groups or Private (/find, /filter, /search)
    @bot.on_message(filters.command(["find", "filter", "search"]))
    async def filter_command_handler(client: Client, message: Message):
        parts = message.text.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            await message.reply_text("💡 **Usage:** `/find <movie name>`\nExample: `/find Buddy`", quote=True)
            return

        query = parts[1].strip()
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        await execute_movie_search(client, message, query, is_group=is_group)

    # 2. Search via Direct Text in Groups and Private Chat
    @bot.on_message(filters.text & ~filters.regex(r"^[!/]"))
    async def direct_text_search_handler(client: Client, message: Message):
        if message.from_user and message.from_user.is_bot:
            return

        user_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)
        raw_text = message.text.strip()

        # If in private chat, make sure user isn't in an active /addmovie upload session
        is_group = str(message.chat.type).lower().endswith("group")
        if not is_group:
            session = await session_mgr.get_session(user_id)
            if session and session.get("status") in ("AWAITING_FIRST_FILE", "ACTIVE"):
                # Active upload session in progress, don't interfere
                return

        # Ignore noise and ultra-short texts
        if len(raw_text) < 2 or raw_text.lower() in IGNORED_WORDS:
            return

        await execute_movie_search(client, message, raw_text, is_group=is_group)

    # Common search executor
    async def execute_movie_search(client: Client, message: Message, query: str, is_group: bool = True):
        try:
            results = await db.search_movies(query)

            if results:
                # If single result in private chat, deliver full movie card directly!
                user_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)
                if not is_group and len(results) == 1:
                    m = results[0]
                    await send_movie_card(client, user_id=user_id, movie_id=m["movie_id"], is_admin=False)
                    return

                # Send list with direct links and buttons
                result_text, result_markup = build_filter_results(query, results, page=1, is_group=is_group)
                await message.reply_text(
                    text=result_text,
                    reply_markup=result_markup,
                    disable_web_page_preview=True,
                    quote=True
                )
            else:
                if len(query) >= 3:
                    not_found = await message.reply_text(
                        f"❌ <b>'{query}'</b> hamare group ya database mein nahi mila!\n\n"
                        f"🔍 Kripya movie ka sahi spelling check karein.",
                        quote=True
                    )
                    # Auto-delete after 30 seconds in groups to keep chat clean
                    if is_group:
                        await asyncio.sleep(30)
                        try:
                            await not_found.delete()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Error in execute_movie_search: {e}")

    # 3. Pagination Callbacks
    @bot.on_callback_query(filters.regex(r"^flt_(\d+)_(.+)"))
    async def filter_pagination_callback(client: Client, query: CallbackQuery):
        match = query.matches[0]
        page = int(match.group(1))
        search_query = match.group(2)

        results = await db.search_movies(search_query)
        if not results:
            await query.answer("Results expire ho gaye!", show_alert=True)
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
            logger.debug(f"Pagination edit notice: {e}")
            await query.answer()
