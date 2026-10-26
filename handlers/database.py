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
}


class Database:
    def __init__(self, db_url: str = None, db_name: str = None):
        self.db_url = db_url or getattr(Config, "DATABASE_URL", None) or getattr(Config, "DB_URI", None)
        self.db_name = db_name or getattr(Config, "DB_NAME", "AshMovieBot")
        self._mem_movies = {}
        self._mem_sessions = {}
        self._mem_bans = {}
        self._mem_warns = {}
        self.has_mongo = False
        self.client = None
        self.db = None
        self.movies_col = None
        self.sessions_col = None
        self.bans_col = None
        self.warns_col = None
        self.force_sub_col = None

        if self.db_url and motor is not None:
            try:
                self.client = motor.motor_asyncio.AsyncIOMotorClient(self.db_url, serverSelectionTimeoutMS=5000)
                self.db = self.client[self.db_name]
                self.movies_col = self.db[getattr(Config, "DB_COLLECTION", "movies")]
                self.sessions_col = self.db["sessions"]
                self.bans_col = self.db["bans"]
                self.warns_col = self.db["warns"]
                self.force_sub_col = self.db["force_sub"]
                self.has_mongo = True
                logger.info("[DB] Mongo connected: %s", self.db_name)
            except Exception as e:
                logger.error("[DB] Mongo connect failed: %s", e)
                self.has_mongo = False

    async def init_indexes(self):
        if not self.has_mongo:
            return
        try:
            await self.movies_col.create_index("movie_id", unique=True)
            await self.movies_col.create_index("normalized_title")
            await self.movies_col.create_index("group_msg_id")
            await self.sessions_col.create_index("admin_id", unique=True)
        except Exception as e:
            logger.warning("[DB] indexes: %s", e)

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

    async def find_movie_by_title_year(self, title: str, year=None) -> Optional[dict]:
        """Same normalized title + same year → existing movie (for merge / no duplicate)."""
        norm = re.sub(r"[^\w\s]", " ", (title or "").lower())
        norm = re.sub(r"\s+", " ", norm).strip()
        if not norm:
            return None
        year_val = None
        if year is not None and str(year).isdigit():
            year_val = int(year)

        def _match(doc):
            dnorm = (doc.get("normalized_title") or "")
            dnorm = re.sub(r"[^\w\s]", " ", dnorm.lower())
            dnorm = re.sub(r"\s+", " ", dnorm).strip()
            if dnorm != norm:
                t2 = re.sub(r"[^\w\s]", " ", (doc.get("title") or "").lower())
                t2 = re.sub(r"\s+", " ", t2).strip()
                if t2 != norm:
                    return False
            dy = doc.get("year")
            if year_val is None and (dy is None or dy == "" or dy == "N/A"):
                return True
            try:
                return int(dy) == year_val if dy is not None and str(dy).isdigit() else (year_val is None)
            except Exception:
                return year_val is None

        if self.has_mongo and self.movies_col is not None:
            try:
                best = None
                best_ts = ""
                async for doc in self.movies_col.find({}):
                    if not _match(doc):
                        continue
                    ts = str(doc.get("added_at") or doc.get("updated_at") or "")
                    if best is None or ts >= best_ts:
                        best = doc
                        best_ts = ts
                if best:
                    best["_id"] = str(best.get("_id", ""))
                    return best
            except Exception as e:
                logger.error(f"[DB] find_movie_by_title_year: {e}")

        best = None
        best_ts = ""
        for doc in self._mem_movies.values():
            if not _match(doc):
                continue
            ts = str(doc.get("added_at") or "")
            if best is None or ts >= best_ts:
                best = doc
                best_ts = ts
        return best

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
            mtitle = (m.get("normalized_title") or m.get("title") or "").lower()
            mtitle = re.sub(r"[^\w\s]", " ", mtitle)
            mtitle = re.sub(r"\s+", " ", mtitle).strip()
            m_tokens = set(mtitle.split())
            if not m_tokens:
                continue
            if query_title_tokens:
                hits = sum(1 for t in query_title_tokens if t in m_tokens or any(t in mt for mt in m_tokens))
                if hits < max(1, (len(query_title_tokens) + 1) // 2):
                    continue
            myear = m.get("year")
            if year_tokens and myear is not None:
                if str(myear) not in year_tokens:
                    continue
            results.append(m)
        results.sort(key=lambda x: str(x.get("added_at") or ""), reverse=True)
        return results

    async def delete_movie(self, movie_id: str) -> bool:
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.delete_one({"movie_id": movie_id})
            except Exception as e:
                logger.error(f"[DB] delete_movie: {e}")
        self._mem_movies.pop(movie_id, None)
        return True

    async def delete_by_group_msg_id(self, msg_id: int) -> list:
        deleted = []
        if self.has_mongo and self.movies_col is not None:
            try:
                async for doc in self.movies_col.find({"group_msg_id": msg_id}):
                    mid = doc.get("movie_id")
                    await self.movies_col.delete_one({"movie_id": mid})
                    deleted.append(mid)
                    self._mem_movies.pop(mid, None)
            except Exception as e:
                logger.error(f"[DB] delete_by_group_msg_id: {e}")
        return deleted

    async def get_ban_status(self, user_id: int) -> dict:
        if self.has_mongo and self.bans_col is not None:
            try:
                doc = await self.bans_col.find_one({"user_id": user_id})
                if doc:
                    return {"is_banned": True, "reason": doc.get("reason", "")}
            except Exception:
                pass
        b = self._mem_bans.get(user_id)
        if b:
            return {"is_banned": True, "reason": b.get("reason", "")}
        return {"is_banned": False}

    async def is_banned(self, user_id: int) -> bool:
        st = await self.get_ban_status(user_id)
        return bool(st.get("is_banned"))

    async def add_warning(self, user_id: int, by: int, reason: str = "") -> int:
        count = 1
        if self.has_mongo and self.warns_col is not None:
            try:
                await self.warns_col.insert_one({
                    "user_id": user_id, "by": by, "reason": reason,
                    "at": datetime.datetime.utcnow().isoformat(),
                })
                count = await self.warns_col.count_documents({"user_id": user_id})
            except Exception as e:
                logger.error(f"[DB] add_warning: {e}")
        else:
            self._mem_warns.setdefault(user_id, []).append({"by": by, "reason": reason})
            count = len(self._mem_warns[user_id])
        return count

    async def get_warnings_count(self, user_id: int) -> int:
        if self.has_mongo and self.warns_col is not None:
            try:
                return await self.warns_col.count_documents({"user_id": user_id})
            except Exception:
                pass
        return len(self._mem_warns.get(user_id, []))

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
        skip = max(0, (page - 1) * per_page)
        out = []
        if self.has_mongo and self.movies_col is not None:
            try:
                async for doc in self.movies_col.find({}).sort("added_at", -1).skip(skip).limit(per_page):
                    doc["_id"] = str(doc.get("_id", ""))
                    out.append(doc)
                return out
            except Exception as e:
                logger.error(f"[DB] list_movies: {e}")
        items = sorted(self._mem_movies.values(), key=lambda x: str(x.get("added_at") or ""), reverse=True)
        return items[skip: skip + per_page]

    async def get_active_session(self, admin_id: int):
        if self.has_mongo and self.sessions_col is not None:
            try:
                doc = await self.sessions_col.find_one({"admin_id": admin_id})
                if doc:
                    return doc.get("session")
            except Exception as e:
                logger.error(f"[DB] get_active_session: {e}")
        return self._mem_sessions.get(admin_id)

    async def set_active_session(self, admin_id: int, session: dict):
        self._mem_sessions[admin_id] = session
        if self.has_mongo and self.sessions_col is not None:
            try:
                await self.sessions_col.update_one(
                    {"admin_id": admin_id},
                    {"$set": {"admin_id": admin_id, "session": session}},
                    upsert=True,
                )
            except Exception as e:
                logger.error(f"[DB] set_active_session: {e}")

    async def clear_active_session(self, admin_id: int):
        self._mem_sessions.pop(admin_id, None)
        if self.has_mongo and self.sessions_col is not None:
            try:
                await self.sessions_col.delete_one({"admin_id": admin_id})
            except Exception as e:
                logger.error(f"[DB] clear_active_session: {e}")

    async def get_all_active_sessions(self) -> list:
        return list(self._mem_sessions.items())

    async def sync_from_group_and_channel(self, bot, limit: int = 500):
        return 0


db = Database()
