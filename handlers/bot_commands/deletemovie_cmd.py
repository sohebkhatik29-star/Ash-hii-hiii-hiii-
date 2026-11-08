# ==============================================================================
# handlers/bot_commands/deletemovie_cmd.py
# Command: /deletemovie <name>  (aliases: /delmovie, /dm, /deletemovies)
# Example: /deletemovie boss
#   → same name ki SAARI movies (kisi bhi year / language) filter list se delete
# Admin only. Group post bhi delete try karta hai.
# ==============================================================================

import logging
import re
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config
from handlers.database import db

logger = logging.getLogger("DeleteMovieCmd")


def _norm(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _name_matches(movie_title: str, query: str) -> bool:
    """
    'boss' matches: Boss, Boss (2013), Boss (Bengali), Boss (Bengali) (2013)
    Does NOT match: Boss 2, Boss Level, The Boss Baby
    """
    q = _norm(query)
    if not q:
        return False
    n = _norm(movie_title)
    if not n:
        return False
    if n == q:
        return True
    # Boss (Bengali) / Boss (2013) style
    if n.startswith(q + " "):
        rest = n[len(q):].strip()
        # only allow year or (lang) suffix, not another word like "2"/"level"
        if re.match(r"^(\(?\d{4}\)?|\([a-z]+\)|\([a-z]+\)\s*\(?\d{4}\)?)?$", rest):
            return True
        # "boss bengali" after strip of parens already in norm → "boss bengali"
        # if rest is a single language token, allow
        langs = {
            "hindi", "english", "tamil", "telugu", "malayalam", "kannada",
            "marathi", "bengali", "punjabi", "urdu", "bhojpuri",
        }
        if rest in langs:
            return True
        if re.match(r"^[a-z]+\s+\d{4}$", rest) and rest.split()[0] in langs:
            return True
    return False


async def _is_authorized(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    user_id = message.from_user.id

    try:
        from handlers.admins_db import sync_admins_to_config
        await sync_admins_to_config()
    except Exception:
        pass

    try:
        if user_id in (Config.ADMINS or set()):
            return True
    except Exception:
        pass

    try:
        if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            member = await client.get_chat_member(message.chat.id, user_id)
            st = member.status
            if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
            st_s = str(getattr(st, "value", st) or st).lower()
            if any(x in st_s for x in ("administrator", "owner", "creator")):
                return True
    except Exception as e:
        logger.debug("deletemovie admin check: %s", e)

    return False


async def _find_movies_by_name(query: str) -> list:
    """Return list of movie docs matching name (any year/language)."""
    matched = []
    try:
        if getattr(db, "has_mongo", False) and getattr(db, "movies_col", None) is not None:
            async for doc in db.movies_col.find({}):
                title = doc.get("title") or doc.get("normalized_title") or ""
                if _name_matches(title, query):
                    matched.append(doc)
        mem = getattr(db, "_mem_movies", None)
        if isinstance(mem, dict):
            seen = {d.get("movie_id") for d in matched}
            for mid, doc in list(mem.items()):
                if mid in seen:
                    continue
                title = (doc or {}).get("title") or (doc or {}).get("normalized_title") or ""
                if _name_matches(title, query):
                    matched.append(doc)
    except Exception as e:
        logger.error("find by name: %s", e)
    return matched


async def _delete_movie_full(bot: Client, doc: dict) -> bool:
    """Delete DB entry + try delete group post."""
    mid = doc.get("movie_id")
    if not mid:
        return False
    # group post
    g_chat = doc.get("group_chat_id")
    g_msg = doc.get("group_msg_id")
    if g_chat and g_msg:
        try:
            await bot.delete_messages(int(g_chat), int(g_msg))
        except Exception as e:
            logger.debug("delete group post %s: %s", mid, e)
    # DB
    try:
        if hasattr(db, "delete_movie"):
            await db.delete_movie(mid)
        elif getattr(db, "movies_col", None) is not None:
            await db.movies_col.delete_one({"movie_id": mid})
        mem = getattr(db, "_mem_movies", None)
        if isinstance(mem, dict):
            mem.pop(mid, None)
        return True
    except Exception as e:
        logger.error("delete movie %s: %s", mid, e)
        return False


def register_deletemovie_command(bot: Client):
    """Register /deletemovie <name> — delete all filters for that movie name."""

    @bot.on_message(
        filters.command(["deletemovie", "delmovie", "dm", "deletemovies"])
        & (filters.group | filters.private),
        group=-3,
    )
    async def deletemovie_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return

            if not await _is_authorized(client, message):
                return

            # parse name from command args
            parts = (message.text or message.caption or "").split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                try:
                    await message.reply_text(
                        "Usage:\n"
                        "<code>/deletemovie boss</code>\n\n"
                        "Is naam ki saari movies (kisi bhi year/language) filter se delete ho jayengi.",
                        quote=True,
                    )
                except Exception:
                    pass
                return

            query = parts[1].strip()
            # drop accidental leading /
            if query.startswith("/"):
                query = query.lstrip("/")

            matched = await _find_movies_by_name(query)
            if not matched:
                try:
                    await message.reply_text(
                        f"Koi filter nahi mila naam se: <b>{query}</b>",
                        quote=True,
                    )
                except Exception:
                    pass
                return

            titles = []
            ok = 0
            for doc in matched:
                t = doc.get("title") or "?"
                y = doc.get("year")
                label = f"{t}" + (f" ({y})" if y else "")
                titles.append(label)
                if await _delete_movie_full(client, doc):
                    ok += 1

            # delete admin command message
            try:
                await message.delete()
            except Exception:
                pass

            summary = "\n".join(f"• {t}" for t in titles[:30])
            extra = f"\n… +{len(titles) - 30} more" if len(titles) > 30 else ""
            try:
                await client.send_message(
                    message.chat.id,
                    f"🗑 <b>Deleted {ok}/{len(matched)}</b> filter(s) for <b>{query}</b>:\n\n"
                    f"{summary}{extra}",
                )
            except Exception:
                pass

            logger.info(
                "deletemovie query=%r deleted=%s/%s by=%s",
                query, ok, len(matched), message.from_user.id,
            )
        except Exception as e:
            logger.error("deletemovie handler: %s: %s", type(e).__name__, e)
