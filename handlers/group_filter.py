# ============================================================================
# handlers/group_filter.py  — ONLY filter / search
# Style: movie name + Link 🔗 https://t.me/c/... (group poster post)
# Same title+year = ek; alag year = alag line
# ============================================================================
import asyncio
import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery
from configs import Config
from handlers.database import db
from handlers.force_sub_handler import enforce_force_sub_in_group

logger = logging.getLogger("GroupFilter")
AUTO_DELETE_SECONDS = int(getattr(Config, "AUTO_DELETE_TIME", 1800) or 1800)


def _norm_title(t) -> str:
    t = (t or "").lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def dedupe_by_title_year(results: list) -> list:
    """Same title + same year → sirf ek (latest). Alag year alag rahe."""
    best = {}
    for m in results or []:
        key = (_norm_title(m.get("title")), str(m.get("year") or ""))
        prev = best.get(key)
        if prev is None:
            best[key] = m
            continue
        a = str(m.get("added_at") or "")
        b = str(prev.get("added_at") or "")
        if a >= b:
            best[key] = m
    return list(best.values())


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
    """
    Format jaise dusre group:
    1. Movie Name (Year)
    Link 🔗 https://t.me/c/..../msg_id
    """
    lines = [f"🔎 <b>Results for:</b> <code>{query}</code>", f"📄 Total: {len(results)}", ""]
    for i, m in enumerate(results, start=1):
        title = m.get("title") or "Unknown"
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


async def _schedule_delete(client, chat_id, message_ids, delay=None):
    delay = delay if delay is not None else AUTO_DELETE_SECONDS
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception:
            pass


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

        results = await db.search_movies(query)
        if not isinstance(results, list):
            results = list(results or [])
        results = dedupe_by_title_year(results)

        if not results:
            m = await message.reply_text(f"❌ <b>{query}</b> nahi mila.", quote=True)
            asyncio.create_task(_schedule_delete(client, message.chat.id, [m.id, message.id], 60))
            return

        # Plain text + group poster link only (no buttons)
        text = build_link_results_text(query, results[:20])
        sent = await message.reply_text(
            text,
            quote=True,
            disable_web_page_preview=True,
        )
        if sent:
            asyncio.create_task(
                _schedule_delete(client, message.chat.id, [sent.id], AUTO_DELETE_SECONDS)
            )

    @bot.on_callback_query(filters.regex(r"^close_card$"))
    async def close_card_callback(client: Client, query: CallbackQuery):
        try:
            await query.message.delete()
        except Exception:
            pass
        await query.answer()
