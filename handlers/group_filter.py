# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/group_filter.py
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, CallbackQuery
from configs import Config
from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.send_file import send_movie_card

logger = logging.getLogger("GroupFilter")

PAGE_SIZE = 8
AUTO_DELETE_SECONDS = 2 * 60  # 2 min testing; change to 24*60*60 later
_pending_not_found: dict = {}

async def _get_all_movies() -> dict:
    mem = getattr(db, "_mem_movies", None) or {}
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

IGNORED_WORDS = {
    "hi", "hello", "hey", "hlo", "hii", "hiii", "ok", "okay", "bye", "gm", "gn",
    "ha", "haa", "nahi", "no", "yes", "haan", "admin", "thanks", "thank you",
    "thx", "tq", "good", "nice", "please", "plz", "pls", "link", "bot", "movie",
    "film", "bhai", "bro", "sir", "ji", "kya", "hai", "ho", "ka", "ki", "ke",
    "aur", "or", "the", "a", "an", "to", "of", "in", "on", "for", "done"
}

def _get_post_link(m: dict) -> str:
    post_link = m.get("group_post_link") or ""
    if post_link and "t.me/c/" in str(post_link):
        return post_link
    group_id = m.get("group_chat_id") or getattr(Config, "MOVIE_GROUP_ID", None)
    msg_id = m.get("group_msg_id")
    if msg_id and group_id:
        try:
            return db.make_telegram_post_link(group_id, msg_id)
        except Exception:
            pass
    return ""

def build_filter_results(query: str, results: list, page: int = 1, is_group: bool = True):
    filtered = []
    for m in results:
        link = _get_post_link(m)
        if not link or "t.me/c/" not in link:
            continue
        filtered.append(m)
    dedup = {}
    for m in filtered:
        title_key = (m.get("normalized_title") or (m.get("title") or "").lower()).strip()
        year_key = m.get("year") or 0
        key = (title_key, year_key)
        prev = dedup.get(key)
        if not prev:
            dedup[key] = m
        else:
            prev_id = prev.get("group_msg_id") or 0
            cur_id = m.get("group_msg_id") or 0
            if cur_id >= prev_id:
                dedup[key] = m
    filtered = list(dedup.values())
    filtered.sort(key=lambda x: (x.get("year") or 9999, x.get("title") or ""))
    total = len(filtered)
    if total == 0:
        return None, None
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total)
    page_results = filtered[start_idx:end_idx]
    text = f"\ud83c\udfac <b>Results for:</b> <code>{query}</code>\n"
    text += f"\ud83d\udcc4 <b>Page {page}/{total_pages}</b> \u2022 Total: <b>{total}</b>\n\n"
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
        text += f"\n\ud83d\udd17 Link {post_link}\n\n"
    return text, None

async def _safe_delete(client: Client, chat_id: int, message_ids: list):
    for mid in message_ids:
        if not mid:
            continue
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass

async def _schedule_delete(client: Client, chat_id: int, message_ids: list, delay: int = AUTO_DELETE_SECONDS):
    try:
        await asyncio.sleep(delay)
        await _safe_delete(client, chat_id, message_ids)
    except Exception as e:
        logger.debug(f"schedule_delete: {e}")

def register_group_filter_handlers(bot: Client):

    @bot.on_message(filters.command(["find", "filter", "search"]))
    async def find_command(client: Client, message: Message):
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("Usage: /find movie name", quote=True)
            return
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        await execute_movie_search(client, message, parts[1].strip(), is_group=is_group)

    @bot.on_message(filters.command("done") & filters.group)
    async def done_command(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        if user_id not in Config.ADMINS:
            return
        try:
            await message.delete()
        except Exception:
            pass
        replied = message.reply_to_message
        if not replied:
            return
        try:
            not_found_id = _pending_not_found.pop(replied.id, None)
            if not_found_id:
                try:
                    await client.delete_messages(message.chat.id, not_found_id)
                except Exception:
                    pass
            confirm = await replied.reply_text(
                "\u2705 <b>Your request has been uploaded.</b>\n\nPlease check it in the group. Thank you!",
                quote=True
            )
            asyncio.create_task(
                _schedule_delete(client, message.chat.id, [replied.id, confirm.id], AUTO_DELETE_SECONDS)
            )
        except Exception as e:
            logger.error(f"/done error: {e}")

    @bot.on_message(filters.command("dl") & filters.group)
    async def dl_command(client: Client, message: Message):
        user_id = message.from_user.id if message.from_user else 0
        if user_id not in Config.ADMINS:
            return
        replied = message.reply_to_message
        if not replied:
            await message.reply_text("Poster pe reply karke /dl bhejo.", quote=True)
            return
        try:
            await message.delete()
        except Exception:
            pass
        target_msg_id = replied.id
        all_movies = await _get_all_movies()
        deleted = None
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
                    logger.info(f"[AUTO-DL] Post deleted -> filter removed: {title} (msg_id={msg_id})")
        except Exception as e:
            logger.error(f"on_movie_post_deleted error: {e}")

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
        raw_text = (message.text or "").strip()
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if not is_group:
            session = await session_mgr.get_session(user_id)
            if session and session.get("status") in ("AWAITING_FIRST_FILE", "ACTIVE"):
                return
        lower = raw_text.lower()
        if len(raw_text) < 2 or lower in IGNORED_WORDS:
            return
        if len(raw_text) <= 3 and not any(c.isalpha() for c in raw_text):
            return
        await execute_movie_search(client, message, raw_text, is_group=is_group)

    async def execute_movie_search(client: Client, message: Message, query: str, is_group: bool = True):
        try:
            results = await db.search_movies(query)
            if not results:
                if len(query) >= 3:
                    not_found = await message.reply_text(
                        f"\u274c <b>'{query}'</b> group / database mein nahi mila!\n\n"
                        f"\ud83d\udd0d Spelling check karein ya thoda short name try karein.\n"
                        f"\u23f3 Request save hai \u2014 upload hone ke baad admin /done se notify karega.",
                        quote=True
                    )
                    if is_group:
                        _pending_not_found[message.id] = not_found.id
                        asyncio.create_task(
                            _schedule_delete(client, message.chat.id, [not_found.id], AUTO_DELETE_SECONDS)
                        )
                return
            if not is_group and len(results) == 1:
                user_id = message.from_user.id if message.from_user else 0
                await send_movie_card(client, user_id=user_id, movie_id=results[0]["movie_id"], is_admin=False)
                return
            result_text, result_markup = build_filter_results(query, results, page=1, is_group=is_group)
            if not result_text:
                not_found = await message.reply_text(
                    f"\u274c <b>'{query}'</b> group / database mein nahi mila!\n\n"
                    f"\ud83d\udd0d Spelling check karein ya thoda short name try karein.\n"
                    f"\u23f3 Request save hai \u2014 upload hone ke baad admin /done se notify karega.",
                    quote=True
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
                quote=True
            )
            if is_group:
                asyncio.create_task(
                    _schedule_delete(client, message.chat.id, [message.id, bot_msg.id], AUTO_DELETE_SECONDS)
                )
        except Exception as e:
            logger.error(f"Error in execute_movie_search: {e}")

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
        if not result_text:
            await query.answer("No group posts found.", show_alert=True)
            return
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

    @bot.on_callback_query(filters.regex(r"^close_card$"))
    async def close_card_callback(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.message.edit_text("\u274c Closed")
            except Exception:
                pass
        await query.answer()
