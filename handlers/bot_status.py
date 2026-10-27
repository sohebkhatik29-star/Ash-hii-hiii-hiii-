# handlers/bot_status.py — Bot Status stats + filters list/search helpers
import logging
from handlers.database import db

logger = logging.getLogger("BotStatus")

# Ensure users collection methods exist (used by check_user_status too)
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
    """users, movies (filters), total quality files."""
    users = 0
    movies = 0
    files = 0
    try:
        users = await db.total_users_count()
    except Exception:
        users = 0
    try:
        movies = await db.total_movies_count()
    except Exception:
        movies = 0
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
    if year:
        return f"{title} ({year})"
    return title


async def list_filters_page(page: int = 1, per_page: int = 10) -> tuple:
    """Returns (movies_list, total_count, page, total_pages)."""
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
