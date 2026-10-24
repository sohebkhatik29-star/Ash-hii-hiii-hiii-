# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/group_filter.py
# Purpose: Universal Movie Search & Group AutoFilter (Rose / AutoFilter style).
#          Works in Groups, Supergroups, and Private Chat.
#          When user types movie name → shows group post links (jump to existing post).
#          Auto-deletes search messages after 24h. /done marks fulfilled requests.
#          /dl reply on poster deletes that movie filter from database.
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

async def _get_all_movies() -> dict:
    """Return current movie map; pull from Mongo if available."""
    mem = getattr(db, "_mem_movies", None)
    if mem is None:
        mem = {}
    if getattr(db, "has_mongo", False) and getattr(db, "movies_col", None) is not None:
        try:
            async for doc in db.movies_col.find({}):
                mid = doc.get("movie_id")
                if mid:
                    doc.pop("_id", None)
                    mem[mid] = doc
            db._mem_movies = mem
        except Exception as e:
            logger.warning(f"[DL] mongo load: {e}")
    return mem


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
            "✅ <b>Request Uploaded Successfully!</b>\n\n"
            "🎬 Aapki requested movie ab group me available hai.\n"
            "🔍 Movie ka naam search karke dekh lo.",
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

    # 2b. /dl — admin replies to a movie POSTER to delete its filter from DB
    @bot.on_message(filters.command("dl") & filters.group)
    async def delete_filter_handler(client: Client, message: Message):
        """
        Admin: reply to the movie poster/card in group with /dl
        → remove that movie from search database
        → so searching its name no longer shows this result
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
                "⚠️ <b>/dl</b> ko movie ke <b>poster / card</b> pe reply karke bhejo.\n"
                "Usse us movie ka filter database se delete ho jayega.",
                quote=True
            )
            await asyncio.sleep(20)
            await _safe_delete(client, message.chat.id, [message.id, tip.id])
            return

        target = message.reply_to_message
        text = (target.caption or target.text or "").strip()
        msg_id = target.id

        deleted_title = None
        movie_id_to_delete = None

        movies = await _get_all_movies()
        for mid, m in list(movies.items()):
            if m.get("group_msg_id") == msg_id:
                movie_id_to_delete = mid
                deleted_title = m.get("title", "Unknown")
                break
            link = m.get("group_post_link") or ""
            if link.endswith(f"/{msg_id}"):
                movie_id_to_delete = mid
                deleted_title = m.get("title", "Unknown")
                break

        # Fallback: parse title from caption
        if not movie_id_to_delete and text:
            title_match = re.search(r"🎬\s*(?:<b>)?([^\n<(]+)(?:</b>)?", text)
            if title_match:
                raw_title = title_match.group(1).strip()
                clean_title = re.sub(r"\(\d{4}\)", "", raw_title).strip()
                for mid, m in list(movies.items()):
                    if m.get("title", "").lower() == clean_title.lower():
                        movie_id_to_delete = mid
                        deleted_title = m.get("title", clean_title)
                        break
                    if m.get("normalized_title", "") == clean_title.lower():
                        movie_id_to_delete = mid
                        deleted_title = m.get("title", clean_title)
                        break

        # Delete admin /dl command
        try:
            await message.delete()
        except Exception:
            pass

        if not movie_id_to_delete:
            tip = await client.send_message(
                message.chat.id,
                "❌ Is post ka filter database me nahi mila.\n"
                "Sirf bot ke movie poster pe /dl reply karein.",
            )
            await asyncio.sleep(15)
            await _safe_delete(client, message.chat.id, [tip.id])
            return

        await db.delete_movie(movie_id_to_delete)
        confirm = await client.send_message(
            message.chat.id,
            f"🗑️ <b>Filter deleted!</b>\n\n"
            f"🎬 <b>{deleted_title}</b>\n"
            f"Ab search results me yeh movie nahi aayegi.",
        )
        asyncio.create_task(
            _schedule_delete(client, message.chat.id, [confirm.id], 30)
        )
        logger.info(f"[DL] Admin {admin_id} deleted filter: {deleted_title} ({movie_id_to_delete})")

    # 3. Direct text search (auto-filter style) – works only when Group Privacy is OFF
    @bot.on_message(
        filters.text
        & ~filters.command(["start", "help", "find", "filter", "search", "addmovie",
                            "newmovie", "finish", "cancel", "status", "movies",
                            "delete", "stats", "broadcast", "generatelink", "done", "dl"])
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
