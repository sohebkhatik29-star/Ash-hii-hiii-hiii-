# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/database.py  (PLAIN — no b64/zlib)
# ==============================================================================

import logging
import re
import time
from typing import List, Optional, Dict, Any

import motor.motor_asyncio
from configs import Config

logger = logging.getLogger("Database")

STOPWORDS = {
    "movie", "movies", "film", "films", "full", "hd", "bluray", "webrip", "web-dl",
    "dvdrip", "brrip", "hdtv", "x264", "x265", "hevc", "aac", "mp4", "mkv",
    "dual", "audio", "hindi", "english", "tamil", "telugu", "malayalam",
    "480p", "720p", "1080p", "2160p", "4k", "uhd", "cam", "ts", "tc",
    "the", "a", "an", "of", "and", "or", "in", "on", "to", "for", "with",
}

class MovieDatabase:
    def __init__(self):
        self.has_mongo = False
        self.client = None
        self.db = None
        self.movies_col = None
        self.users_col = None
        self.warns_col = None
        self.force_sub_col = None
        self.settings_col = None
        self.sessions_col = None
        self._mem_force_channels: List[dict] = []
        self._mem_force_msg = None
        self._mem_movies: List[dict] = []
        try:
            url = getattr(Config, "DATABASE_URL", None) or getattr(Config, "DB_URL", None)
            name = getattr(Config, "DATABASE_NAME", None) or getattr(Config, "DB_NAME", "moviefilterbot")
            if url:
                self.client = motor.motor_asyncio.AsyncIOMotorClient(url, serverSelectionTimeoutMS=8000)
                self.db = self.client[name]
                self.movies_col = self.db["movies"]
                self.users_col = self.db["users"]
                self.warns_col = self.db["warns"]
                self.force_sub_col = self.db["force_sub_channels"]
                self.settings_col = self.db["bot_settings"]
                self.sessions_col = self.db["sessions"]
                self.has_mongo = True
                logger.info("MongoDB connected: %s", name)
        except Exception as e:
            logger.error("Mongo init failed: %s", e)
            self.has_mongo = False

    async def init_indexes(self):
        if not self.has_mongo:
            return
        try:
            await self.movies_col.create_index([("title", "text"), ("file_name", "text")])
            await self.movies_col.create_index("message_id")
            await self.movies_col.create_index("file_unique_id")
            await self.users_col.create_index("user_id", unique=True)
            await self.warns_col.create_index("user_id")
            await self.force_sub_col.create_index("channel_id", unique=True)
        except Exception as e:
            logger.warning("init_indexes: %s", e)

    async def add_movie(self, movie_data: dict) -> bool:
        if not movie_data:
            return False
        movie_data.setdefault("added_at", int(time.time()))
        if self.has_mongo and self.movies_col is not None:
            try:
                fid = movie_data.get("file_unique_id") or movie_data.get("message_id")
                if fid:
                    await self.movies_col.update_one(
                        {"file_unique_id": fid} if movie_data.get("file_unique_id") else {"message_id": movie_data.get("message_id")},
                        {"$set": movie_data},
                        upsert=True,
                    )
                else:
                    await self.movies_col.insert_one(movie_data)
                return True
            except Exception as e:
                logger.error("add_movie: %s", e)
        self._mem_movies.append(movie_data)
        return True

    async def search_movies(self, query: str, page: int = 1, per_page: int = 10) -> dict:
        q = (query or "").strip()
        if not q:
            return {"results": [], "total": 0, "page": page}
        tokens = [t.lower() for t in re.split(r"[\s\-_\.]+", q) if t and t.lower() not in STOPWORDS and len(t) > 1]
        if not tokens:
            tokens = [q.lower()]
        results = []
        total = 0
        if self.has_mongo and self.movies_col is not None:
            try:
                and_clauses = []
                for tok in tokens:
                    and_clauses.append({
                        "$or": [
                            {"title": {"$regex": re.escape(tok), "$options": "i"}},
                            {"file_name": {"$regex": re.escape(tok), "$options": "i"}},
                            {"caption": {"$regex": re.escape(tok), "$options": "i"}},
                        ]
                    })
                filt = {"$and": and_clauses} if len(and_clauses) > 1 else (and_clauses[0] if and_clauses else {})
                total = await self.movies_col.count_documents(filt)
                cursor = self.movies_col.find(filt).sort("added_at", -1).skip(max(0, (page - 1) * per_page)).limit(per_page)
                async for doc in cursor:
                    doc["_id"] = str(doc.get("_id", ""))
                    results.append(doc)
            except Exception as e:
                logger.error("search_movies: %s", e)
        else:
            ql = q.lower()
            for m in self._mem_movies:
                hay = f"{m.get('title','')} {m.get('file_name','')} {m.get('caption','')}".lower()
                if all(t in hay for t in tokens):
                    results.append(m)
            total = len(results)
            results = results[(page - 1) * per_page: page * per_page]
        return {"results": results, "total": total, "page": page, "per_page": per_page}

    async def get_movie_by_id(self, movie_id: str):
        if self.has_mongo and self.movies_col is not None:
            try:
                from bson import ObjectId
                doc = await self.movies_col.find_one({"_id": ObjectId(movie_id)})
                if doc:
                    doc["_id"] = str(doc["_id"])
                    return doc
            except Exception:
                pass
            try:
                doc = await self.movies_col.find_one({"file_unique_id": movie_id})
                if doc:
                    doc["_id"] = str(doc.get("_id", ""))
                    return doc
            except Exception:
                pass
        return None

    async def delete_movie(self, movie_id: str) -> bool:
        if self.has_mongo and self.movies_col is not None:
            try:
                from bson import ObjectId
                res = await self.movies_col.delete_one({"_id": ObjectId(movie_id)})
                return res.deleted_count > 0
            except Exception:
                try:
                    res = await self.movies_col.delete_one({"file_unique_id": movie_id})
                    return res.deleted_count > 0
                except Exception as e:
                    logger.error("delete_movie: %s", e)
        return False

    async def sync_from_group_and_channel(self, bot=None):
        return

    async def add_user(self, user_id: int, name: str = ""):
        if self.has_mongo and self.users_col is not None:
            try:
                await self.users_col.update_one(
                    {"user_id": int(user_id)},
                    {"$set": {"user_id": int(user_id), "name": name, "last_seen": int(time.time())},
                     "$setOnInsert": {"joined_at": int(time.time())}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("add_user: %s", e)

    async def is_banned(self, user_id: int) -> bool:
        if user_id in getattr(Config, "BANNED_USERS", set()):
            return True
        if self.has_mongo and self.users_col is not None:
            try:
                doc = await self.users_col.find_one({"user_id": int(user_id), "banned": True})
                return bool(doc)
            except Exception:
                pass
        return False

    async def ban_user(self, user_id: int, reason: str = ""):
        if self.has_mongo and self.users_col is not None:
            try:
                await self.users_col.update_one(
                    {"user_id": int(user_id)},
                    {"$set": {"banned": True, "ban_reason": reason, "banned_at": int(time.time())}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("ban_user: %s", e)

    async def unban_user(self, user_id: int):
        if self.has_mongo and self.users_col is not None:
            try:
                await self.users_col.update_one(
                    {"user_id": int(user_id)},
                    {"$set": {"banned": False}, "$unset": {"ban_reason": "", "banned_at": ""}},
                )
            except Exception as e:
                logger.warning("unban_user: %s", e)

    async def add_warn(self, user_id: int, by: int = 0, reason: str = "") -> int:
        count = 0
        if self.has_mongo and self.warns_col is not None:
            try:
                await self.warns_col.insert_one({
                    "user_id": int(user_id), "by": by, "reason": reason, "at": int(time.time())
                })
                count = await self.warns_col.count_documents({"user_id": int(user_id)})
            except Exception as e:
                logger.warning("add_warn: %s", e)
        return count

    async def get_warns(self, user_id: int) -> int:
        if self.has_mongo and self.warns_col is not None:
            try:
                return await self.warns_col.count_documents({"user_id": int(user_id)})
            except Exception:
                pass
        return 0

    async def clear_warns(self, user_id: int):
        if self.has_mongo and self.warns_col is not None:
            try:
                await self.warns_col.delete_many({"user_id": int(user_id)})
            except Exception:
                pass

    async def save_session(self, admin_id: int, data: dict):
        if self.has_mongo and self.sessions_col is not None:
            try:
                await self.sessions_col.update_one(
                    {"admin_id": int(admin_id)},
                    {"$set": {**data, "admin_id": int(admin_id), "updated_at": int(time.time())}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning("save_session: %s", e)

    async def get_session(self, admin_id: int):
        if self.has_mongo and self.sessions_col is not None:
            try:
                return await self.sessions_col.find_one({"admin_id": int(admin_id)})
            except Exception:
                pass
        return None

    async def delete_session(self, admin_id: int):
        if self.has_mongo and self.sessions_col is not None:
            try:
                await self.sessions_col.delete_one({"admin_id": int(admin_id)})
            except Exception:
                pass

    async def list_sessions(self) -> List[dict]:
        out = []
        if self.has_mongo and self.sessions_col is not None:
            try:
                async for d in self.sessions_col.find({}):
                    out.append(d)
            except Exception:
                pass
        return out


db = MovieDatabase()
