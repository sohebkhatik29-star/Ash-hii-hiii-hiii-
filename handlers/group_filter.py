# Temporary stub — bot must start. Full search restored next commit.
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from configs import Config

logger = logging.getLogger("GroupFilter")

def register_group_filter_handlers(bot: Client):
    @bot.on_message(filters.command(["find", "filter", "search"]))
    async def find_command(client: Client, message: Message):
        link = getattr(Config, "MOVIE_GROUP_LINK", "") or "https://t.me/+GvaNlRbEZtc5MzQ1"
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if not is_group:
            await message.reply_text(
                "\U0001f3ac Movie search sirf <b>group</b> me allowed hai.\n\nGroup join karke search karo:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3ac Open Movie Group", url=link)]]),
                quote=True,
            )
            return
        await message.reply_text("Search temporarily disabled during fix. Try again in 2 min.", quote=True)

    @bot.on_message(filters.text & ~filters.command([
        "start", "help", "addmovie", "done", "dl", "warn", "sp", "rls", "n_available", "prm",
        "add_force_chnl", "del_force_chnl", "see_force_sub_chnl", "set_force_sub_message",
        "find", "filter", "search",
    ]) & ~filters.via_bot)
    async def direct_text_search_handler(client: Client, message: Message):
        if not message.from_user or message.from_user.is_bot:
            return
        raw = (message.text or "").strip()
        if len(raw) < 2:
            return
        is_group = message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        if not is_group:
            link = getattr(Config, "MOVIE_GROUP_LINK", "") or "https://t.me/+GvaNlRbEZtc5MzQ1"
            await message.reply_text(
                "\U0001f3ac Movie search sirf <b>group</b> me allowed hai.\n\nGroup join karke search karo:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3ac Open Movie Group", url=link)]]),
                quote=True,
            )
            return
        # group: try real search if db available
        try:
            from handlers.database import db
            from handlers.force_sub_handler import enforce_force_sub_in_group
            blocked = await enforce_force_sub_in_group(client, message, query=raw)
            if blocked:
                return
            results = await db.search_movies(raw)
            if not results:
                await message.reply_text(f"\u274c <b>'{raw}'</b> nahi mila.", quote=True)
                return
            lines = [f"\U0001f3ac <b>Results for:</b> <code>{raw}</code>\n"]
            for i, m in enumerate(results[:8], 1):
                title = m.get("title") or "?"
                year = m.get("year") or ""
                link = m.get("group_post_link") or ""
                lines.append(f"<b>{i}. {title}</b>" + (f" ({year})" if year else "") + (f"\n\U0001f517 {link}" if link else ""))
            await message.reply_text("\n\n".join(lines), quote=True, disable_web_page_preview=True)
        except Exception as e:
            logger.exception("search")
            await message.reply_text(f"Search error: {e}", quote=True)
