# ============================================================================
# handlers/group_filter.py  — ONLY filter / search
# Style: movie name + Link 🔗 https://t.me/c/... (group poster post)
# Query: noise hatao, pura title strict match (search_query.py)
# Delete: auto_delete.py (testing 2 min) — bot reply + user message dono
# ============================================================================
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from configs import Config
from handlers.database import db
from handlers.force_sub_handler import enforce_force_sub_in_group
from handlers.search_query import clean_search_query, filter_strict_matches
from handlers.auto_delete import schedule_result_delete, schedule_not_found_delete
import time
import secrets

logger = logging.getLogger("GroupFilter")

# Pagination: 10 results per page, Next/Prev buttons
PAGE_SIZE = 10
_SEARCH_CACHE: dict = {}  # cache_id -> {query, results, ts}
_SEARCH_CACHE_TTL = 600  # 10 min


def _purge_search_cache():
    now = time.time()
    for k, v in list(_SEARCH_CACHE.items()):
        if now - v.get("ts", 0) > _SEARCH_CACHE_TTL:
            _SEARCH_CACHE.pop(k, None)


def _store_search(query: str, results: list) -> str:
    _purge_search_cache()
    cid = secrets.token_hex(4)
    _SEARCH_CACHE[cid] = {"query": query, "results": results, "ts": time.time()}
    return cid


def _norm_title(t) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _display_title(raw: str) -> str:
    """Channel brand / tech noise hatao display ke liye."""
    t = str(raw or "")
    t = re.sub(r"@[\w_]+", " ", t)
    t = re.sub(
        r"(?i)\b(toonworld\d*all|movies?[\w_]*\d+|cinemaghar|netflix[\w_]*\d*|"
        r"channeldata\w*|dk\s*drama|movies?safari|ash\s*movies?[\w_]*)\b",
        " ",
        t,
    )
    t = re.sub(
        r"(?i)\b(2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|hevc|x264|x265|"
        r"webrip|web-dl|webdl|bluray|uncut|dual\s*audio|hindi|english|esub|"
        r"10bit|8bit|mkv|mp4|amzn|web)\b",
        " ",
        t,
    )
    t = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", t)
    t = re.sub(r"[\[\]\(\)\{\}_.\-]+", " ", t)
    return re.sub(r"\s+", " ", t).strip() or str(raw or "Unknown")


def dedupe_by_title_year(results: list) -> list:
    """Same clean title + same year → sirf ek. Prefer record with valid group link (latest)."""
    best = {}
    for m in results or []:
        key = (_norm_title(_display_title(m.get("title"))), str(m.get("year") or ""))
        prev = best.get(key)
        if prev is None:
            best[key] = m
            continue
        m_link = bool(m.get("group_msg_id") or m.get("group_post_link"))
        p_link = bool(prev.get("group_msg_id") or prev.get("group_post_link"))
        if m_link and not p_link:
            best[key] = m
            continue
        if p_link and not m_link:
            continue
        # Both have / both missing link → keep latest (fresh group_msg_id more likely valid)
        a = str(m.get("updated_at") or m.get("added_at") or "")
        b = str(prev.get("updated_at") or prev.get("added_at") or "")
        if a >= b:
            best[key] = m
    out = list(best.values())
    out.sort(key=lambda x: str(x.get("added_at") or ""))  # display: pehle upload upar
    return out


def _get_post_link(m: dict) -> str:
    post_link = m.get("group_post_link") or ""
    if post_link and "t.me" in str(post_link):
        return str(post_link).strip()
    group_id = m.get("group_chat_id") or getattr(Config, "MOVIE_GROUP_ID", None)
    msg_id = m.get("group_msg_id")
    if msg_id and group_id:
        try:
            return db.make_telegram_post_link(int(group_id), int(msg_id))
        except Exception:
            pass
    return ""


def build_link_results_page(query: str, results: list, page: int = 0) -> tuple:
    """Return (text, reply_markup) for one page of results. 10 per page."""
    total = len(results or [])
    if total <= 0:
        return f"❌ <b>{query}</b> nahi mila.", None

    page = max(0, int(page or 0))
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page >= total_pages:
        page = total_pages - 1

    start = page * PAGE_SIZE
    chunk = results[start : start + PAGE_SIZE]

    lines = [
        f"🔎 <b>Results for:</b> <code>{query}</code>",
        f"📄 Page {page + 1}/{total_pages} — Total: {total}",
        "",
    ]
    for i, m in enumerate(chunk, start=start + 1):
        title = _display_title(m.get("title") or "Unknown")
        year = m.get("year")
        name = f"{title}" + (f" ({year})" if year else "")
        link = _get_post_link(m)
        # one compact block per result
        if link:
            lines.append(f"{i}. <b>{name}</b>\nLink 🔗 {link}")
        else:
            lines.append(f"{i}. <b>{name}</b>\nLink 🔗 (post abhi available nahi)")
        lines.append("")
    text = "\n".join(lines).strip()
    return text, page, total_pages


def build_page_keyboard(cache_id: str, page: int, total_pages: int):
    if total_pages <= 1:
        return None
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"sp|{cache_id}|{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("Next ▶️", callback_data=f"sp|{cache_id}|{page + 1}"))
    return InlineKeyboardMarkup([row]) if row else None


def register_group_filter_handlers(bot: Client):

    @bot.on_message(filters.command(["find", "search", "filter"]) & filters.group)
    async def cmd_search(client: Client, message: Message):
        parts = (message.text or "").split(maxsplit=1)
        query = parts[1].strip() if len(parts) > 1 else ""
        if not query:
            await message.reply_text("Usage: /find movie name", quote=True)
            return
        await execute_movie_search(client, message, query, is_group=True)

    @bot.on_message(filters.text & filters.group & ~filters.command([
        "start", "help", "addmovie", "cancel", "finish", "find", "search", "filter",
        "broadcast", "ban", "unban", "status", "dl", "delete",
    ]))
    async def direct_text_search(client: Client, message: Message):
        if not message.from_user or message.from_user.is_bot:
            return
        text = (message.text or "").strip()
        if not text or len(text) < 2:
            return
        if text.startswith("/") or text.startswith("@"):
            return
        await execute_movie_search(client, message, text, is_group=True)

    async def execute_movie_search(client: Client, message: Message, query: str, is_group: bool = True):
        user = message.from_user
        if not user:
            return
        if not is_group or message.chat.type.name == "PRIVATE":
            link = getattr(Config, "MOVIE_GROUP_LINK", "") or "https://t.me/"
            await message.reply_text(f"🎬 Movies sirf group me milengi.\nJoin: {link}", quote=True)
            return
        try:
            blocked = await enforce_force_sub_in_group(client, message)
            if blocked:
                return
        except Exception as e:
            logger.debug("force_sub: %s", e)
        try:
            ban = await db.get_ban_status(user.id)
            if ban.get("is_banned"):
                await message.reply_text("🚫 Aap ban ho. Results nahi milenge.", quote=True)
                return
        except Exception:
            pass

        # 1) Noise hatao — message se movie name extract
        cleaned = clean_search_query(query)
        search_q = cleaned if cleaned else query.strip()

        if not search_q or len(search_q) < 2:
            m = await message.reply_text(f"❌ Movie name samajh nahi aaya.", quote=True)
            # bot reply + user message → 2 min
            schedule_not_found_delete(client, message.chat.id, [m.id, message.id])
            return

        results = await db.search_movies(search_q)
        if not isinstance(results, list):
            results = list(results or [])

        # 2) Strict full-name match — short partial reject
        results = filter_strict_matches(search_q, results)
        results = dedupe_by_title_year(results)

        if not results:
            m = await message.reply_text(f"❌ <b>{query}</b> nahi mila.", quote=True)
            # bot reply + user message → 2 min
            schedule_not_found_delete(client, message.chat.id, [m.id, message.id])
            try:
                from handlers.bot_commands.dn_cmd import track_bot_reply
                track_bot_reply(message.chat.id, message.id, m.id, client=client)
            except Exception:
                pass
            # Admin ko request forward + details (for /dn or /done later)
            try:
                from handlers.bot_commands.dn_cmd import notify_admin_movie_request
                await notify_admin_movie_request(
                    client, message, query, not_found_msg_id=getattr(m, "id", None)
                )
            except Exception as e:
                logger = __import__("logging").getLogger("GroupFilter")
                logger.debug("notify_admin_movie_request: %s", e)
            return

        cache_id = _store_search(query, results)
        text, page, total_pages = build_link_results_page(query, results, page=0)
        kb = build_page_keyboard(cache_id, page, total_pages)
        sent = await message.reply_text(
            text,
            quote=True,
            disable_web_page_preview=True,
            reply_markup=kb,
        )
        if sent:
            # bot result + user message → 2 min
            schedule_result_delete(client, message.chat.id, [sent.id, message.id])
            try:
                from handlers.bot_commands.dn_cmd import track_bot_reply
                track_bot_reply(message.chat.id, message.id, sent.id, client=client)
            except Exception:
                pass

    @bot.on_callback_query(filters.regex(r"^sp\|"))
    async def search_page_callback(client: Client, query: CallbackQuery):
        """Next / Prev page for search results."""
        try:
            data = (query.data or "").split("|")
            # sp|{cache_id}|{page}
            if len(data) < 3:
                await query.answer("Expired", show_alert=False)
                return
            cache_id = data[1]
            try:
                page = int(data[2])
            except Exception:
                page = 0
            entry = _SEARCH_CACHE.get(cache_id)
            if not entry:
                await query.answer("Results expire ho gaye — dubara search karo.", show_alert=True)
                return
            results = entry.get("results") or []
            qtext = entry.get("query") or ""
            text, page, total_pages = build_link_results_page(qtext, results, page=page)
            kb = build_page_keyboard(cache_id, page, total_pages)
            try:
                await query.message.edit_text(
                    text,
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
            except Exception as e:
                # same content / message not modified
                logger.debug("page edit: %s", e)
            await query.answer(f"Page {page + 1}/{total_pages}")
        except Exception as e:
            logger.warning("search_page_callback: %s", e)
            try:
                await query.answer("Error", show_alert=False)
            except Exception:
                pass

    @bot.on_callback_query(filters.regex(r"^close_card$"))
    async def close_card_callback(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer()
