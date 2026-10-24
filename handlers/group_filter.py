# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/group_filter.py
# Purpose: Group auto-filter — movie name search returns direct t.me/c/ group links.
#          Auto-delete query+reply after 2 min. /dl removes filter. Post delete = /dl.
# ==============================================================================

import asyncio
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatType

from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.send_file import send_movie_card

logger = logging.getLogger("GroupFilter")

# 2 minutes for testing — change to 24*60*60 for production
AUTO_DELETE_SECONDS = 2 * 60

# Track not-found bot messages so /done can delete them
_pending_not_found: dict = {}


def _get_group_link(m: dict) -> str:
    """Return ONLY direct group post link (t.me/c/...). Never bot deep-link."""
    post_link = m.get("group_post_link") or ""
    if post_link and "t.me/c/" in str(post_link):
        return post_link
    chat_id = m.get("group_chat_id")
    msg_id = m.get("group_msg_id")
    if chat_id and msg_id:
        try:
            return db.make_telegram_post_link(chat_id, msg_id)
        except Exception:
            pass
    return ""


def build_filter_results(query: str, results: list, page: int = 1, is_group: bool = True):
    """Build text + optional pagination. Only t.me/c/ links, dedupe by group_msg_id."""
    per_page = 10
    # Filter + dedupe
    seen = set()
    clean = []
    for m in results:
        link = _get_group_link(m)
        if not link or "t.me/c/" not in link:
            continue
        key = m.get("group_msg_id") or link
        if key in seen:
            continue
        seen.add(key)
        clean.append(m)

    total = len(clean)
    if total == 0:
        return None, None

    start = (page - 1) * per_page
    page_items = clean[start:start + per_page]
    total_pages = max(1, (total + per_page - 1) // per_page)

    lines = [f"\ud83d\udd0d <b>Results for:</b> <code>{query}</code>\n"]
    for i, m in enumerate(page_items, start=start + 1):
        title = m.get("title") or "Unknown"
        year = m.get("year")
        year_str = f" ({year})" if year else ""
        link = _get_group_link(m)
        lines.append(f"{i}. <b>{title}{year_str}</b>\n   ➡️ {link}")

    if total_pages > 1:
        lines.append(f"\n\ud83d\udcc4 Page {page}/{total_pages}")

    text = "\n".join(lines)
    markup = None
    if total_pages > 1:
        buttons = []
        row = []
        if page > 1:
            row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"flt_{page-1}_{query[:40]}"))
        if page < total_pages:
            row.append(InlineKeyboardButton("Next ➡️", callback_data=f"flt_{page+1}_{query[:40]}"))
        if row:
            buttons.append(row)
        markup = InlineKeyboardMarkup(buttons)
    return text, markup


async def _get_all_movies() -> dict:
    try:
        if hasattr(db, "_mem_movies"):
            return dict(db._mem_movies)
    except Exception:
        pass
    return {}


async def _schedule_delete(client: Client, chat_id: int, message_ids: list, delay: int = AUTO_DELETE_SECONDS):
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass


def register_group_filter_handlers(bot: Client):

    # ------------------------------------------------------------------
    # /done — mark upload complete, delete previous not-found if any
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("done") & filters.group)
    async def done_command(client: Client, message: Message):
        try:
            replied = message.reply_to_message
            if not replied:
                await message.reply_text("Kisi movie query pe reply karke /done bhejo.", quote=True)
                return
            # Delete previous not-found bot message if tracked
            not_found_id = _pending_not_found.pop(replied.id, None)
            if not_found_id:
                try:
                    await client.delete_messages(message.chat.id, not_found_id)
                except Exception:
                    pass
            confirm = await message.reply_text(
                "✅ Your movie will be uploaded soon!\nAdmin ko notify kar diya gaya hai.",
                quote=True,
            )
            asyncio.create_task(
                _schedule_delete(client, message.chat.id, [replied.id, confirm.id, message.id], AUTO_DELETE_SECONDS)
            )
        except Exception as e:
            logger.error(f"done_command: {e}")

    # ------------------------------------------------------------------
    # /dl — reply to movie poster to remove filter from DB
    # ------------------------------------------------------------------
    @bot.on_message(filters.command("dl") & filters.group)
    async def dl_command(client: Client, message: Message):
        try:
            replied = message.reply_to_message
            if not replied:
                await message.reply_text("Poster pe reply karke /dl bhejo.", quote=True)
                return
            target_msg_id = replied.id
            deleted = None
            if hasattr(db, "delete_by_group_msg_id"):
                titles = await db.delete_by_group_msg_id(target_msg_id)
                if titles:
                    deleted = {"title": titles[0]}
            else:
                all_movies = await _get_all_movies()
                for mid, m in list(all_movies.items()):
                    if m.get("group_msg_id") == target_msg_id:
                        deleted = m
                        await db.delete_movie(mid)
                        break
                if not deleted:
                    caption = (replied.caption or replied.text or "")[:80]
                    for mid, m in list(all_movies.items()):
                        title = (m.get("title") or "").lower()
                        if title and title in caption.lower():
                            deleted = m
                            await db.delete_movie(mid)
                            break
            if deleted:
                msg = await client.send_message(
                    message.chat.id,
                    f"\ud83d\uddd1\ufe0f Filter deleted: <b>{deleted.get('title', '?')}</b>\nAb search results me yeh movie nahi aayegi."
                )
                asyncio.create_task(_schedule_delete(client, message.chat.id, [msg.id], AUTO_DELETE_SECONDS))
            else:
                msg = await client.send_message(message.chat.id, "\u274c Is poster ka filter database me nahi mila.")
                asyncio.create_task(_schedule_delete(client, message.chat.id, [msg.id], AUTO_DELETE_SECONDS))
        except Exception as e:
            logger.error(f"dl_command: {e}")

    # ------------------------------------------------------------------
    # When a movie post is deleted from the group → remove it from filter DB
    # (same effect as /dl)
    # ------------------------------------------------------------------
    @bot.on_deleted_messages(filters.group)
    async def on_movie_post_deleted(client: Client, messages):
        try:
            deleted_ids = set()
            for m in messages:
                if m and getattr(m, "id", None):
                    try:
                        deleted_ids.add(int(m.id))
                    except (TypeError, ValueError):
                        pass
            if not deleted_ids:
                return
            for msg_id in deleted_ids:
                titles = []
                if hasattr(db, "delete_by_group_msg_id"):
                    titles = await db.delete_by_group_msg_id(msg_id)
                else:
                    all_movies = await _get_all_movies()
                    for mid, m in list(all_movies.items()):
                        gmsg = m.get("group_msg_id")
                        try:
                            if gmsg is not None and int(gmsg) == msg_id:
                                titles.append(m.get("title", "?"))
                                await db.delete_movie(mid)
                        except (TypeError, ValueError):
                            continue
                for title in titles:
                    logger.info(f"[AUTO-DL] Post deleted → filter removed: {title} (msg_id={msg_id})")
        except Exception as e:
            logger.error(f"on_movie_post_deleted error: {e}")

    # ------------------------------------------------------------------
    # Direct text search in groups / private
    # ------------------------------------------------------------------
    @bot.on_message(
        filters.text
        & ~filters.command(["start", "help", "addmovie", "newmovie", "cancel", "finish",
                            "delete", "stats", "broadcast", "generatelink", "done", "dl"])
        & ~filters.via_bot
    )
    async def direct_text_search_handler(client: Client, message: Message):
        if message.from_user and message.from_user.is_bot:
            return
        if message.sender_chat and not message.from_user:
            return
        user_id = message.from_user.id if message.from_user else 0
        query = (message.text or "").strip()
        if len(query) < 2:
            return
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        await execute_movie_search(client, message, query, is_group)

    async def execute_movie_search(client: Client, message: Message, query: str, is_group: bool):
        try:
            results = await db.search_movies(query)
            if results:
                result_text, result_markup = build_filter_results(query, results, page=1, is_group=is_group)
                if not result_text:
                    # All results lacked valid t.me/c/ links
                    not_found = await message.reply_text(
                        f"\u274c <b>'{query}'</b> hamare group ya database mein nahi mila!\n\n"
                        f"\ud83d\udd0d Kripya movie ka sahi spelling check karein.",
                        quote=True,
                    )
                    if is_group:
                        _pending_not_found[message.id] = not_found.id
                        asyncio.create_task(
                            _schedule_delete(client, message.chat.id, [not_found.id], AUTO_DELETE_SECONDS)
                        )
                    return
                bot_msg = await message.reply_text(
                    text=result_text,
                    reply_markup=result_markup,
                    disable_web_page_preview=True,
                    quote=True,
                )
                if is_group:
                    asyncio.create_task(
                        _schedule_delete(client, message.chat.id, [message.id, bot_msg.id], AUTO_DELETE_SECONDS)
                    )
            else:
                if len(query) >= 3:
                    not_found = await message.reply_text(
                        f"\u274c <b>'{query}'</b> hamare group ya database mein nahi mila!\n\n"
                        f"\ud83d\udd0d Kripya movie ka sahi spelling check karein.\n"
                        f"Upload ke baad /done reply karke batao.",
                        quote=True,
                    )
                    if is_group:
                        _pending_not_found[message.id] = not_found.id
                        asyncio.create_task(
                            _schedule_delete(client, message.chat.id, [not_found.id], AUTO_DELETE_SECONDS)
                        )
        except Exception as e:
            logger.error(f"Error in execute_movie_search: {e}")

    # Pagination callbacks
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
            if result_text:
                await query.message.edit_text(
                    text=result_text,
                    reply_markup=result_markup,
                    disable_web_page_preview=True,
                )
            await query.answer()
        except Exception as e:
            logger.debug(f"Pagination edit notice: {e}")
            await query.answer()
