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
from handlers.auto_delete import schedule_result_delete, schedule_not_found_delete, schedule_suggest_delete
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
            # TMDB + OMDb + Gemini — teeno se search, merge
            try:
                from handlers.tmdb_suggest import (
                    multi_source_search,
                    store_suggestions,
                    build_suggest_text,
                    build_suggest_keyboard,
                )
                suggestions = await multi_source_search(search_q, limit=5)
                if not suggestions and query and query.strip() != search_q:
                    suggestions = await multi_source_search(query.strip(), limit=5)
            except Exception as e:
                logger.warning("suggest pipeline: %s", e)
                suggestions = []

            # Spelling already correct (exact TMDB title) → no suggestion buttons
            exact_ok = False
            exact_item = None
            if suggestions:
                try:
                    from handlers.tmdb_suggest import (
                        has_exact_tmdb_match,
                        get_exact_match_item,
                        is_unreleased,
                        build_unreleased_text,
                        build_unreleased_keyboard,
                    )
                    exact_item = get_exact_match_item(query, suggestions) or get_exact_match_item(
                        search_q, suggestions
                    )
                    exact_ok = exact_item is not None or has_exact_tmdb_match(
                        query, suggestions
                    ) or has_exact_tmdb_match(search_q, suggestions)
                except Exception as e:
                    logger.warning("exact/unreleased check: %s", e)
                    exact_ok = False
                    exact_item = None

            # Exact title + future release date → not released yet
            if exact_item and is_unreleased(exact_item):
                text = build_unreleased_text(query, exact_item)
                kb = build_unreleased_keyboard(exact_item)
                m = await message.reply_text(
                    text,
                    quote=True,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                if m:
                    schedule_not_found_delete(client, message.chat.id, [m.id, message.id])
                    try:
                        from handlers.bot_commands.dn_cmd import track_bot_reply
                        track_bot_reply(message.chat.id, message.id, m.id, client=client)
                    except Exception:
                        pass
                return

            if suggestions and not exact_ok:
                cid = store_suggestions(
                    suggestions,
                    meta={
                        "user_id": user.id,
                        "chat_id": message.chat.id,
                        "user_msg_id": message.id,
                        "query": query,
                    },
                )
                text = build_suggest_text(query)
                kb = build_suggest_keyboard(cid, suggestions)
                m = await message.reply_text(
                    text,
                    quote=True,
                    reply_markup=kb,
                    disable_web_page_preview=True,
                )
                if m:
                    # Options panel: 1 minute me auto-delete (agar select nahi kiya)
                    schedule_suggest_delete(client, message.chat.id, [m.id, message.id], 60)
                    try:
                        from handlers.bot_commands.dn_cmd import track_bot_reply
                        track_bot_reply(message.chat.id, message.id, m.id, client=client)
                    except Exception:
                        pass
                return

            m = await message.reply_text(
                f"❌ <b>{query}</b> abhi mere paas available nahi hai.\n\n"
                f"Request admin ke paas bhej di gayi hai — admin ke daalne ka wait karo, "
                f"ya khud admin se request kar sakte ho.",
                quote=True,
            )
            # bot reply + user message → auto delete
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

    @bot.on_callback_query(filters.regex(r"^sp\|"), group=-3)
    async def search_page_callback(client: Client, query: CallbackQuery):
        """Next / Prev page for search results."""
        try:
            data = (query.data or "").split("|")
            # sp|{cache_id}|{page}
            if len(data) < 3:
                try:
                    await query.answer("Expired", show_alert=False)
                except Exception:
                    pass
                return
            cache_id = data[1]
            try:
                page = int(data[2])
            except Exception:
                page = 0

            entry = _SEARCH_CACHE.get(cache_id)
            results = (entry or {}).get("results") or []
            qtext = (entry or {}).get("query") or ""

            # Cache miss (Render restart) → recover query from message text + re-search
            if not results or not qtext:
                body = query.message.text or query.message.caption or ""
                m = re.search(
                    r"Results for:\s*(?:<code>)?([^<\n]+)",
                    body,
                    re.I,
                )
                if m:
                    qtext = m.group(1).strip().strip("`")
                if qtext:
                    try:
                        results = await db.search_movies(qtext)
                        if not isinstance(results, list):
                            results = list(results or [])
                        results = filter_strict_matches(qtext, results)
                        results = dedupe_by_title_year(results)
                        # refresh cache under same id
                        _SEARCH_CACHE[cache_id] = {
                            "query": qtext,
                            "results": results,
                            "ts": time.time(),
                        }
                    except Exception as e:
                        logger.warning("page re-search: %s", e)
                        results = []

            if not results:
                try:
                    await query.answer(
                        "Results expire — dubara movie name search karo.",
                        show_alert=True,
                    )
                except Exception:
                    pass
                return

            text, page, total_pages = build_link_results_page(qtext, results, page=page)
            kb = build_page_keyboard(cache_id, page, total_pages)
            try:
                await query.message.edit_text(
                    text,
                    disable_web_page_preview=True,
                    reply_markup=kb,
                )
            except Exception as e:
                err = str(e).lower()
                if "not modified" not in err and "message is not modified" not in err:
                    logger.warning("page edit: %s", e)
            try:
                await query.answer(f"Page {page + 1}/{total_pages}")
            except Exception:
                pass
        except Exception as e:
            logger.warning("search_page_callback: %s", e)
            try:
                await query.answer("Error", show_alert=False)
            except Exception:
                pass

    @bot.on_callback_query(filters.regex(r"^ts_close$"), group=-3)
    async def tmdb_suggest_close(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await query.answer()
        except Exception:
            pass

    @bot.on_callback_query(filters.regex(r"^ts\|"), group=-3)
    async def tmdb_suggest_callback(client: Client, query: CallbackQuery):
        """User picked a TMDB suggestion → search DB; else not-available + admin notify."""
        try:
            parts = (query.data or "").split("|")
            # ts|{cache_id}|{index}
            if len(parts) < 3:
                await query.answer("Expired", show_alert=False)
                return
            cache_id = parts[1]
            try:
                idx = int(parts[2])
            except Exception:
                idx = 0
            from handlers.tmdb_suggest import get_suggestion
            item = get_suggestion(cache_id, idx)
            if not item:
                await query.answer(
                    "Options expire ho gaye — dubara search karo.",
                    show_alert=True,
                )
                return

            title = (item.get("title") or "").strip()
            year = (item.get("year") or "").strip()
            search_q = f"{title} {year}".strip() if year else title
            label = item.get("label") or search_q

            await query.answer(f"Searching: {label[:40]}...")

            results = await db.search_movies(search_q)
            if not isinstance(results, list):
                results = list(results or [])
            results = filter_strict_matches(search_q, results)
            # year-flexible: if empty, try title only
            if not results and year:
                results = await db.search_movies(title)
                if not isinstance(results, list):
                    results = list(results or [])
                results = filter_strict_matches(title, results)
            results = dedupe_by_title_year(results)

            if results:
                from handlers.tmdb_suggest import store_suggestions  # noqa: F401
                # reuse pagination path
                cache_id2 = _store_search(label, results)
                text, page, total_pages = build_link_results_page(label, results, page=0)
                kb = build_page_keyboard(cache_id2, page, total_pages)
                try:
                    await query.message.edit_text(
                        text,
                        disable_web_page_preview=True,
                        reply_markup=kb,
                    )
                except Exception as e:
                    logger.warning("suggest result edit: %s", e)
                    await query.message.reply_text(
                        text,
                        disable_web_page_preview=True,
                        reply_markup=kb,
                        quote=True,
                    )
                return

            # Not in our DB
            msg = (
                "😊 <b>" + str(label) + "</b>\n\n"
                "THIS MOVIE IS NOT AVAILABLE IN MY DATABASE.\n\n"
                "😊 Yeh movie mujhe mere database me nahi mili.\n\n"
                "Request admin ke paas bhej di gayi hai — "
                "admin ke daalne ka wait karo, ya khud admin se request kar sakte ho."
            )
            try:
                await query.message.edit_text(msg, disable_web_page_preview=True)
            except Exception:
                try:
                    await query.message.reply_text(msg, quote=True)
                except Exception:
                    pass
            try:
                from handlers.tmdb_suggest import get_suggest_meta
                from configs import Config
                meta = get_suggest_meta(cache_id)
                uid = meta.get("user_id") or (query.from_user.id if query.from_user else 0)
                uname = ""
                if query.from_user:
                    uname = ("@" + query.from_user.username) if query.from_user.username else (query.from_user.first_name or "")
                admin_text = (
                    "📩 <b>Movie Request (not found)</b>\n"
                    "Movie: <code>" + str(label) + "</code>\n"
                    "User: " + str(uname) + " (<code>" + str(uid) + "</code>)\n"
                    "chat=" + str(meta.get("chat_id") or query.message.chat.id)
                    + " msg=" + str(meta.get("user_msg_id") or 0)
                    + " nfmsg=" + str(query.message.id)
                )
                for aid in (Config.ADMINS or set()):
                    try:
                        await client.send_message(int(aid), admin_text)
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("notify after suggest: %s", e)

        except Exception as e:
            logger.warning("tmdb_suggest_callback: %s", e)
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
