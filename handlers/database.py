# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/database.py  (PLAIN - no b64/zlib)
# ==============================================================================
import re
import uuid
import logging
import datetime
from typing import Optional, List, Dict, Any

try:
    import motor.motor_asyncio
except ImportError:
    motor = None

from configs import Config

logger = logging.getLogger("AshMovieBot.DB")

STOPWORDS = {
    "dedo", "de", "do", "please", "plz", "pls", "movie", "movies",
    "film", "films", "full", "hd", "print", "bhai", "bro", "sir", "ji",
    "kya", "hai", "ka", "ki", "ke", "ko", "se", "me", "mein",
    "aur", "or", "the", "a", "an", "to", "of", "in", "on", "for",
    "send", "bhejo", "bhej", "chahiye", "chahie", "need", "want",
    "download", "link", "file", "files", "quality", "hindi", "english",
    "tamil", "telugu", "dubbed", "dual", "audio", "ok", "okay", "yaar",
    "yar", "dost", "friend", "group", "bot", "admin", "thanks", "thank",
    "thx", "tq", "good", "nice", "hello", "hi", "hey", "hii", "hlo",
    "kese", "kaise", "how", "are", "you", "kr", "kar", "dena",
    "mil", "mile", "mila", "available", "he", "h", "krdo", "kardo",
    "mujhe", "mujh", "help", "pleasee", "namaste", "namaskar",
    "bhejdo", "bhejde", "dijiye", "kripya", "krupaya", "kindly",
    "with", "from", "this", "that", "year", "new", "latest", "old",
    "free", "paid", "premium",
}


class Database:
    def __init__(self, db_url: str = None, db_name: str = None):
        self.has_mongo = False
        self.client = None
        self.db = None
        self.movies_col = None
        self.users_col = None
        self.warns_col = None
        self.sessions_col = None
        self._mem_movies = {}
        self._mem_sessions = {}
        url = db_url or getattr(Config, "DATABASE_URL", None) or getattr(Config, "DB_URL", None)
        name = db_name or getattr(Config, "DATABASE_NAME", None) or getattr(Config, "DB_NAME", "AshMovieBot")
        if motor and url:
            try:
                self.client = motor.motor_asyncio.AsyncIOMotorClient(url)
                self.db = self.client[name]
                self.movies_col = self.db[getattr(Config, "DB_COLLECTION", "movies")]
                self.users_col = self.db["users"]
                self.warns_col = self.db["warns"]
                self.sessions_col = self.db["sessions"]
                self.has_mongo = True
                logger.info("[DB] MongoDB connected")
            except Exception as e:
                logger.error(f"[DB] Mongo connect fail: {e}")
                self.has_mongo = False
        else:
            logger.warning("[DB] Running in memory mode (no Mongo)")

    async def init_indexes(self):
        if not self.has_mongo:
            return
        try:
            await self.movies_col.create_index("movie_id", unique=True)
            await self.movies_col.create_index("normalized_title")
            await self.movies_col.create_index("group_msg_id")
        except Exception as e:
            logger.warning(f"[DB] index: {e}")

    def generate_movie_id(self) -> str:
        return uuid.uuid4().hex[:12]

    async def create_movie(self, title: str, normalized_title: str = None, year: int = None, **extra) -> dict:
        mid = extra.pop("movie_id", None) or self.generate_movie_id()
        norm = normalized_title or re.sub(r"[^\w\s]", "", (title or "").lower()).strip()
        doc = {
            "movie_id": mid,
            "title": title,
            "normalized_title": norm,
            "year": year,
            "qualities": {},
            "poster_file_id": extra.get("poster_file_id"),
            "group_msg_id": None,
            "group_chat_id": None,
            "group_post_link": None,
            "added_at": datetime.datetime.utcnow().isoformat(),
            **{k: v for k, v in extra.items() if k not in ("movie_id", "poster_file_id")},
        }
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.insert_one(doc)
            except Exception as e:
                logger.error(f"[DB] create_movie: {e}")
        self._mem_movies[mid] = doc
        return doc

    async def add_quality_to_movie(self, movie_id: str, quality_key: str, quality_data: dict) -> bool:
        if self.has_mongo and self.movies_col is not None:
            try:
                res = await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {f"qualities.{quality_key}": quality_data}},
                )
                if res.matched_count:
                    if movie_id in self._mem_movies:
                        self._mem_movies[movie_id].setdefault("qualities", {})[quality_key] = quality_data
                    return True
            except Exception as e:
                logger.error(f"[DB] add_quality: {e}")
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id].setdefault("qualities", {})[quality_key] = quality_data
            return True
        return False

    async def get_movie(self, movie_id: str) -> Optional[dict]:
        if self.has_mongo and self.movies_col is not None:
            try:
                doc = await self.movies_col.find_one({"movie_id": movie_id})
                if doc:
                    doc["_id"] = str(doc.get("_id", ""))
                    self._mem_movies[movie_id] = doc
                    return doc
            except Exception as e:
                logger.error(f"[DB] get_movie: {e}")
        return self._mem_movies.get(movie_id)

    async def update_movie_group_post(self, movie_id: str, chat_id: int, msg_id: int) -> str:
        cid = str(chat_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        post_link = f"https://t.me/c/{cid}/{msg_id}"
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {
                        "group_msg_id": msg_id,
                        "group_chat_id": chat_id,
                        "group_post_link": post_link,
                        "updated_at": datetime.datetime.utcnow().isoformat(),
                    }},
                )
            except Exception as e:
                logger.error(f"[DB] update_movie_group_post: {e}")
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id].update({
                "group_msg_id": msg_id,
                "group_chat_id": chat_id,
                "group_post_link": post_link,
            })
        return post_link

    async def search_movies(self, query: str) -> list:
        q_clean = re.sub(r"[^\w\s]", " ", (query or "").lower()).strip()
        raw_tokens = [t for t in q_clean.split() if len(t) > 0]
        q_tokens = [t for t in raw_tokens if t not in STOPWORDS and len(t) >= 2]
        if not q_tokens:
            q_tokens = [t for t in raw_tokens if len(t) >= 3]
        if not q_tokens:
            return []
        year_tokens = {t for t in q_tokens if t.isdigit() and len(t) == 4 and t.startswith(("19", "20"))}
        query_title_tokens = [t for t in q_tokens if t not in year_tokens]
        if not query_title_tokens and not year_tokens:
            return []

        candidates = []
        if self.has_mongo and self.movies_col is not None:
            try:
                async for doc in self.movies_col.find({}):
                    doc["_id"] = str(doc.get("_id", ""))
                    candidates.append(doc)
            except Exception as e:
                logger.error(f"[DB] search load: {e}")
                candidates = list(self._mem_movies.values())
        else:
            candidates = list(self._mem_movies.values())

        results = []
        for m in candidates:
            title = (m.get("title") or "")
            norm = (m.get("normalized_title") or re.sub(r"[^\w\s]", "", title.lower())).strip()
            title_tokens = [t for t in norm.split() if len(t) >= 2]
            if not title_tokens:
                continue
            if year_tokens:
                my = str(m.get("year") or "")
                if my and my not in year_tokens:
                    # allow if year not in title tokens either
                    if not any(yt in title.lower() for yt in year_tokens):
                        continue
            if not query_title_tokens:
                results.append(m)
                continue
            matched = sum(1 for t in query_title_tokens if t in title_tokens or t in norm)
            need = max(1, (len(query_title_tokens) + 1) // 2)  # majority
            if len(query_title_tokens) == 1 and len(query_title_tokens[0]) <= 3:
                # short single token must be exact title word of short title
                if query_title_tokens[0] not in title_tokens:
                    continue
                if len(title_tokens) > 3:
                    continue
            if matched >= need:
                results.append(m)
        # prefer ones with group_msg_id / post link
        results.sort(key=lambda x: (0 if x.get("group_msg_id") or x.get("group_post_link") else 1, x.get("title") or ""))
        return results[:50]

    async def delete_movie(self, movie_id: str) -> bool:
        if self.has_mongo and self.movies_col is not None:
            try:
                res = await self.movies_col.delete_one({"movie_id": movie_id})
                self._mem_movies.pop(movie_id, None)
                return res.deleted_count > 0
            except Exception as e:
                logger.error(f"[DB] delete_movie: {e}")
        return self._mem_movies.pop(movie_id, None) is not None

    async def delete_by_group_msg_id(self, msg_id: int) -> list:
        deleted_titles = []
        if self.has_mongo and self.movies_col is not None:
            try:
                cursor = self.movies_col.find({"group_msg_id": int(msg_id)})
                async for doc in cursor:
                    deleted_titles.append(doc.get("title") or doc.get("movie_id"))
                    await self.movies_col.delete_one({"_id": doc["_id"]})
                    self._mem_movies.pop(doc.get("movie_id"), None)
            except Exception as e:
                logger.error(f"[DB] delete_by_group_msg_id: {e}")
        else:
            for mid, m in list(self._mem_movies.items()):
                if m.get("group_msg_id") == int(msg_id):
                    deleted_titles.append(m.get("title") or mid)
                    del self._mem_movies[mid]
        return deleted_titles

    async def get_ban_status(self, user_id: int) -> dict:
        if user_id in getattr(Config, "BANNED_USERS", []):
            return {"is_banned": True, "reason": "config"}
        if self.has_mongo and self.users_col is not None:
            try:
                doc = await self.users_col.find_one({"user_id": int(user_id)})
                if doc and doc.get("is_banned"):
                    return {"is_banned": True, "reason": doc.get("ban_reason", "")}
            except Exception:
                pass
        return {"is_banned": False, "reason": ""}

    async def is_banned(self, user_id: int) -> bool:
        st = await self.get_ban_status(user_id)
        return bool(st.get("is_banned"))

    async def add_warning(self, user_id: int, by: int, reason: str = "") -> int:
        count = 1
        if self.has_mongo and self.warns_col is not None:
            try:
                await self.warns_col.insert_one({
                    "user_id": int(user_id),
                    "by": int(by),
                    "reason": reason,
                    "at": datetime.datetime.utcnow().isoformat(),
                })
                count = await self.warns_col.count_documents({"user_id": int(user_id)})
            except Exception as e:
                logger.error(f"[DB] add_warning: {e}")
        return count

    async def get_warnings_count(self, user_id: int) -> int:
        if self.has_mongo and self.warns_col is not None:
            try:
                return await self.warns_col.count_documents({"user_id": int(user_id)})
            except Exception:
                pass
        return 0

    def make_telegram_post_link(self, chat_id: int, msg_id: int) -> str:
        cid = str(chat_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        return f"https://t.me/c/{cid}/{msg_id}"

    async def total_movies_count(self) -> int:
        if self.has_mongo and self.movies_col is not None:
            try:
                return await self.movies_col.count_documents({})
            except Exception:
                pass
        return len(self._mem_movies)

    async def list_movies(self, page: int = 1, per_page: int = 20) -> List[dict]:
        out = []
        if self.has_mongo and self.movies_col is not None:
            try:
                cursor = self.movies_col.find({}).sort("added_at", -1).skip(max(0, (page - 1) * per_page)).limit(per_page)
                async for doc in cursor:
                    doc["_id"] = str(doc.get("_id", ""))
                    out.append(doc)
            except Exception as e:
                logger.error(f"[DB] list_movies: {e}")
        else:
            out = list(self._mem_movies.values())[(page - 1) * per_page: page * per_page]
        return out


db = Database(getattr(Config, "DATABASE_URL", None) or getattr(Config, "DB_URL", None),
             getattr(Config, "DATABASE_NAME", None) or getattr(Config, "DB_NAME", "AshMovieBot"))
