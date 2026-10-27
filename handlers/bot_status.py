# handlers/bot_status.py — Bot Status + All Filters (list/search/delete)
import logging
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from handlers.database import db

logger = logging.getLogger("BotStatus")


def _patch_user_methods():
    if getattr(db, "_users_patched", False):
        return
    if not hasattr(db, "_mem_users"):
        db._mem_users = set()

    async def is_user_exist(user_id: int) -> bool:
        user_id = int(user_id)
        if user_id in getattr(db, "_mem_users", set()):
            return True
        if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
            try:
                col = getattr(db, "users_col", None)
                if col is None:
                    db.users_col = db.db.users
                    col = db.users_col
                doc = await col.find_one({"user_id": user_id})
                if doc:
                    db._mem_users.add(user_id)
                    return True
            except Exception as e:
                logger.debug("is_user_exist: %s", e)
        return False

    async def add_user(user_id: int):
        user_id = int(user_id)
        db._mem_users.add(user_id)
        if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
            try:
                col = getattr(db, "users_col", None)
                if col is None:
                    db.users_col = db.db.users
                    col = db.users_col
                await col.update_one(
                    {"user_id": user_id},
                    {"$set": {"user_id": user_id}},
                    upsert=True,
                )
            except Exception as e:
                logger.debug("add_user: %s", e)

    async def total_users_count() -> int:
        if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
            try:
                col = getattr(db, "users_col", None)
                if col is None:
                    db.users_col = db.db.users
                    col = db.users_col
                return await col.count_documents({})
            except Exception as e:
                logger.debug("total_users_count: %s", e)
        return len(getattr(db, "_mem_users", set()) or set())

    if not hasattr(db, "is_user_exist"):
        db.is_user_exist = is_user_exist
    if not hasattr(db, "add_user"):
        db.add_user = add_user
    db.total_users_count = total_users_count
    db._users_patched = True


_patch_user_methods()


async def get_bot_stats() -> dict:
    users = movies = files = 0
    try:
        users = await db.total_users_count()
    except Exception:
        pass
    try:
        movies = await db.total_movies_count()
    except Exception:
        pass
    try:
        if getattr(db, "has_mongo", False) and getattr(db, "movies_col", None) is not None:
            async for doc in db.movies_col.find({}, {"qualities": 1}):
                q = doc.get("qualities") or {}
                if isinstance(q, dict):
                    files += len(q)
        else:
            for m in (getattr(db, "_mem_movies", {}) or {}).values():
                q = m.get("qualities") or {}
                if isinstance(q, dict):
                    files += len(q)
    except Exception as e:
        logger.warning("files count: %s", e)
    return {"users": users, "movies": movies, "files": files}


def movie_label(m: dict) -> str:
    title = (m.get("title") or "Unknown").strip()
    year = m.get("year")
    return f"{title} ({year})" if year else title


async def list_filters_page(page: int = 1, per_page: int = 10):
    page = max(1, int(page or 1))
    per_page = max(1, min(20, int(per_page or 10)))
    try:
        total = await db.total_movies_count()
    except Exception:
        total = 0
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    try:
        movies = await db.list_movies(page=page, per_page=per_page)
    except Exception:
        movies = []
    return movies or [], total, page, total_pages


async def search_filters(query: str, limit: int = 20) -> list:
    q = (query or "").strip()
    if not q:
        return []
    try:
        results = await db.search_movies(q)
    except Exception:
        results = []
    if not isinstance(results, list):
        results = list(results or [])
    return results[:limit]


def kb_st_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 All Filters", callback_data="ap_st_filters:1")],
        [InlineKeyboardButton("🔎 Search Filter", callback_data="ap_st_search")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_home")],
    ])


def kb_filter_actions(movie_id: str, back_data: str = "ap_st_filters:1"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete Filter", callback_data=f"ap_st_del:{movie_id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data=back_data)],
    ])


async def handle_status_callback(client, query, edit_or_reply, ap_state, safe_fn):
    """All ap_st_* callbacks."""
    data = (query.data or "").strip()
    user = query.from_user
    uid = int(user.id) if user else 0

    if data == "ap_st_menu":
        ap_state.pop(uid, None)
        try:
            st = await get_bot_stats()
        except Exception as e:
            logger.warning("stats: %s", e)
            st = {"users": 0, "movies": 0, "files": 0}
        text = (
            "📊 <b>Bot Status</b>\n\n"
            f"👥 Users (bot use): <code>{st.get('users', 0)}</code>\n"
            f"🎬 Filters (movies): <code>{st.get('movies', 0)}</code>\n"
            f"📁 Files saved (qualities): <code>{st.get('files', 0)}</code>\n"
        )
        await edit_or_reply(query, text, kb_st_menu())
        return

    if data.startswith("ap_st_filters:"):
        try:
            page = int(data.split(":")[1])
        except Exception:
            page = 1
        movies, total, page, total_pages = await list_filters_page(page=page, per_page=10)
        if not movies and total == 0:
            await edit_or_reply(query, "Koi filter nahi hai.", kb_st_menu())
            return
        rows = []
        for m in movies:
            mid = m.get("movie_id") or ""
            label = movie_label(m)[:55]
            qs = m.get("qualities") or {}
            nq = len(qs) if isinstance(qs, dict) else 0
            rows.append([InlineKeyboardButton(f"{label} [{nq}f]", callback_data=f"ap_st_view:{mid}:{page}")])
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"ap_st_filters:{page-1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"ap_st_filters:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("🔎 Search", callback_data="ap_st_search")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_st_menu")])
        await edit_or_reply(
            query,
            f"📋 <b>All Filters</b> — page {page}/{total_pages} (total {total})\n\nNaam pe click → Delete / Back:",
            InlineKeyboardMarkup(rows),
        )
        return

    if data == "ap_st_search":
        ap_state[uid] = {"step": "wait_st_search"}
        await edit_or_reply(
            query,
            "🔎 <b>Search Filter</b>\n\nMovie ka naam bhejo:",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="ap_st_menu")]]),
        )
        return

    if data.startswith("ap_st_view:"):
        parts = data.split(":")
        mid = parts[1] if len(parts) > 1 else ""
        try:
            page = int(parts[2]) if len(parts) > 2 else 1
        except Exception:
            page = 1
        back = f"ap_st_filters:{page}"
        try:
            m = await db.get_movie(mid)
        except Exception:
            m = None
        if not m:
            await edit_or_reply(query, "Filter nahi mila.", kb_st_menu())
            return
        title = movie_label(m)
        qs = m.get("qualities") or {}
        qlist = ", ".join(list(qs.keys())[:12]) if isinstance(qs, dict) else "-"
        link = m.get("group_post_link") or ""
        text = (
            f"🎬 <b>{safe_fn(title)}</b>\n"
            f"ID: <code>{mid}</code>\n"
            f"Qualities ({len(qs) if isinstance(qs, dict) else 0}): <code>{safe_fn(qlist)}</code>\n"
        )
        if link:
            text += f"Link: {link}\n"
        await edit_or_reply(query, text, kb_filter_actions(mid, back))
        return

    if data.startswith("ap_st_del:"):
        mid = data.split(":", 1)[1] if ":" in data else ""
        try:
            await db.delete_movie(mid)
            msg = f"✅ Filter delete: <code>{mid}</code>"
        except Exception as e:
            msg = f"❌ Delete fail: {e}"
        await edit_or_reply(query, msg, kb_st_menu())
        return

    await edit_or_reply(query, "📊 <b>Bot Status</b>", kb_st_menu())


async def handle_status_search_input(client, message, ap_state, safe_fn, uid):
    q = (message.text or "").strip()
    if not q:
        await message.reply_text("Movie name bhejo.", quote=True)
        return
    results = await search_filters(q, limit=20)
    ap_state.pop(uid, None)
    if not results:
        await message.reply_text(
            f"❌ <b>{safe_fn(q)}</b> nahi mila.",
            quote=True,
            reply_markup=kb_st_menu(),
        )
        return
    rows = []
    for m in results:
        mid = m.get("movie_id") or ""
        label = movie_label(m)[:55]
        rows.append([InlineKeyboardButton(label, callback_data=f"ap_st_view:{mid}:1")])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="ap_st_menu")])
    await message.reply_text(
        f"🔎 Results for <code>{safe_fn(q)}</code> ({len(results)}):\nNaam pe click → Delete / Back",
        quote=True,
        reply_markup=InlineKeyboardMarkup(rows),
    )
