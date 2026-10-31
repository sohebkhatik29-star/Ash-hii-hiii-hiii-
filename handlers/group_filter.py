# ============================================================================
# handlers/group_filter.py  — ONLY filter / search
# Style: movie name + Link 🔗 https://t.me/c/... (group poster post)
# Query: noise hatao, pura title strict match (search_query.py)
# Delete: auto_delete.py (testing 2 min) — bot reply + user message dono
# ============================================================================
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from configs import Config
from handlers.database import db
from handlers.force_sub_handler import enforce_force_sub_in_group
from handlers.search_query import clean_search_query, filter_strict_matches
from handlers.auto_delete import schedule_result_delete, schedule_not_found_delete

logger = logging.getLogger("GroupFilter")


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


def build_link_results_text(query: str, results: list) -> str:
    lines = [f"🔎 <b>Results for:</b> <code>{query}</code>", f"📄 Total: {len(results)}", ""]
    for i, m in enumerate(results, start=1):
        title = _display_title(m.get("title") or "Unknown")
        year = m.get("year")
        name = f"{title}" + (f" ({year})" if year else "")
        link = _get_post_link(m)
        lines.append(f"{i}. <b>{name}</b>")
        if link:
            lines.append(f"Link 🔗 {link}")
        else:
            lines.append("Link 🔗 (post abhi available nahi)")
        lines.append("")
    return "\n".join(lines).strip()


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
            return

        text = build_link_results_text(query, results[:20])
        sent = await message.reply_text(
            text,
            quote=True,
            disable_web_page_preview=True,
        )
        if sent:
            # bot result + user message → 2 min
            schedule_result_delete(client, message.chat.id, [sent.id, message.id])

    @bot.on_callback_query(filters.regex(r"^close_card$"))
    async def close_card_callback(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer()
