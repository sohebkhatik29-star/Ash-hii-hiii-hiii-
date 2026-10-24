# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/database.py
# Purpose: Dual-Persistent Database Engine (MongoDB Atlas + Local Safe Storage)
#          Ensures zero data loss, survives restarts, and never fails links.
# ==============================================================================

import datetime
import logging
import os
import re
import uuid
import json
import motor.motor_asyncio
from configs import Config

logger = logging.getLogger("Database")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(_BASE, "data")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except OSError:
    DATA_DIR = "/tmp/ash_movie_data"
    os.makedirs(DATA_DIR, exist_ok=True)
MOVIES_FILE = os.path.join(DATA_DIR, "movies.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")


class Database:
    def __init__(self, uri: str, database_name: str):
        self._client = None
        self.db = None
        self.users_col = None
        self.movies_col = None
        self.sessions_col = None
        self.has_mongo = False

        if uri:
            try:
                self._client = motor.motor_asyncio.AsyncIOMotorClient(
                    uri,
                    serverSelectionTimeoutMS=20000,
                    connectTimeoutMS=20000
                )
                self.db = self._client[database_name]
                self.users_col = self.db.users
                self.movies_col = self.db.movies
                self.sessions_col = self.db.movie_sessions
                self.has_mongo = True
            except Exception as e:
                logger.warning(f"[DB] MongoDB init warning: {e}")

        self._mem_users = set()
        self._mem_movies = {}
        self._mem_sessions = {}
        self._load_local_data()

    def _load_local_data(self):
        try:
            if os.path.exists(MOVIES_FILE):
                with open(MOVIES_FILE, "r", encoding="utf-8") as f:
                    self._mem_movies = json.load(f)
            if os.path.exists(SESSIONS_FILE):
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._mem_sessions = {int(k): v for k, v in data.items() if str(k).isdigit()}
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    self._mem_users = set(json.load(f))
            logger.info(f"[DB] Loaded {len(self._mem_movies)} movies & {len(self._mem_sessions)} sessions from local storage.")
        except Exception as e:
            logger.error(f"[DB] Error loading local data: {e}")

    def _save_local_movies(self):
        try:
            with open(MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_movies, f, indent=2)
        except Exception as e:
            logger.error(f"[DB] Failed to save movies to disk: {e}")

    def _save_local_sessions(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._mem_sessions.items()}, f, indent=2)
        except Exception as e:
            logger.error(f"[DB] Failed to save sessions to disk: {e}")

    def _save_local_users(self):
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self._mem_users), f)
        except Exception as e:
            logger.error(f"[DB] Failed to save users to disk: {e}")

    async def init_indexes(self):
        if not self.has_mongo:
            return
        try:
            await self._client.admin.command("ping")
            logger.info("[DB] MongoDB ping OK")
            await self.users_col.create_index("id", unique=True)
            await self.movies_col.create_index("movie_id", unique=True)
            await self.movies_col.create_index("normalized_title")
            await self.sessions_col.create_index("admin_id", unique=True)
            logger.info("[DB] MongoDB indexes checked successfully!")
            count = 0
            async for doc in self.movies_col.find({}):
                mid = doc.get("movie_id")
                if mid:
                    doc.pop("_id", None)
                    self._mem_movies[mid] = doc
                    count += 1
            logger.info(f"[DB] Loaded {count} movies from MongoDB")
            try:
                self._save_local_movies()
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[DB] Index/load failed (Offline fallback active): {e}")
            self.has_mongo = False

    async def add_user(self, id: int):
        self._mem_users.add(int(id))
        self._save_local_users()
        if self.has_mongo:
            try:
                await self.users_col.update_one({"id": int(id)}, {"$set": {"id": int(id)}}, upsert=True)
            except Exception as e:
                logger.warning(f"[DB] add_user: {e}")

    async def is_user_exist(self, id: int) -> bool:
        if int(id) in self._mem_users:
            return True
        if self.has_mongo:
            try:
                doc = await self.users_col.find_one({"id": int(id)})
                if doc:
                    self._mem_users.add(int(id))
                    return True
            except Exception:
                pass
        return False

    async def total_users_count(self) -> int:
        return len(self._mem_users)

    async def get_ban_status(self, id: int):
        return {"is_banned": False, "ban_reason": ""}

    def generate_movie_id(self) -> str:
        return str(uuid.uuid4())[:8]

    async def create_movie(self, title: str, normalized_title: str, year: int = None,
                           languages=None, qualities=None, poster_file_id=None, **extra) -> dict:
        m_id = self.generate_movie_id()
        movie_doc = {
            "movie_id": m_id,
            "title": title,
            "normalized_title": normalized_title or re.sub(r"[^\w\s]", "", (title or "").lower()).strip(),
            "year": year,
            "languages": languages or [],
            "qualities": qualities or {},
            "poster_file_id": poster_file_id,
            "group_msg_id": None,
            "group_chat_id": None,
            "group_post_link": None,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }
        movie_doc.update(extra)
        self._mem_movies[m_id] = movie_doc
        self._save_local_movies()
        if self.has_mongo:
            try:
                await self.movies_col.update_one({"movie_id": m_id}, {"$set": movie_doc}, upsert=True)
            except Exception as e:
                logger.error(f"[DB] create_movie mongo: {e}")
        return movie_doc

    async def get_movie(self, movie_id: str) -> dict:
        if movie_id in self._mem_movies:
            return self._mem_movies[movie_id]
        if self.has_mongo:
            try:
                doc = await self.movies_col.find_one({"movie_id": movie_id})
                if doc:
                    doc.pop("_id", None)
                    self._mem_movies[movie_id] = doc
                    return doc
            except Exception as e:
                logger.warning(f"[DB] get_movie: {e}")
        return None

    async def find_movie_by_title(self, normalized_title: str, year: int = None) -> dict:
        for m in self._mem_movies.values():
            if m.get("normalized_title") == normalized_title:
                if year is None or m.get("year") == year:
                    return m
        if self.has_mongo:
            try:
                q = {"normalized_title": normalized_title}
                if year is not None:
                    q["year"] = year
                doc = await self.movies_col.find_one(q)
                if doc:
                    doc.pop("_id", None)
                    self._mem_movies[doc["movie_id"]] = doc
                    return doc
            except Exception as e:
                logger.warning(f"[DB] find_movie_by_title: {e}")
        return None

    @staticmethod
    def make_telegram_post_link(chat_id: int, msg_id: int) -> str:
        try:
            cid = abs(int(chat_id))
            s = str(cid)
            if s.startswith("100"):
                s = s[3:]
            return f"https://t.me/c/{s}/{msg_id}"
        except Exception:
            return ""

    async def update_movie_group_post(self, movie_id: str, group_chat_id: int, group_msg_id: int):
        post_link = self.make_telegram_post_link(group_chat_id, group_msg_id)
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id]["group_chat_id"] = group_chat_id
            self._mem_movies[movie_id]["group_msg_id"] = group_msg_id
            self._mem_movies[movie_id]["group_post_link"] = post_link
            self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
            self._save_local_movies()
        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {
                        "group_chat_id": group_chat_id,
                        "group_msg_id": group_msg_id,
                        "group_post_link": post_link,
                        "updated_at": datetime.datetime.utcnow().isoformat(),
                    }},
                )
            except Exception as e:
                logger.error(f"[DB] update_movie_group_post: {e}")
        return post_link

    async def search_movies(self, query: str) -> list:
        STOPWORDS = {
            "dedo", "de", "do", "please", "plz", "pls", "movie", "movies",
            "film", "films", "full", "hd", "print", "bhai", "bro", "sir", "ji",
            "kya", "hai", "ho", "ka", "ki", "ke", "ko", "se", "me", "mein",
            "aur", "or", "the", "a", "an", "to", "of", "in", "on", "for",
            "send", "bhejo", "bhej", "chahiye", "chahie", "need", "want",
            "download", "link", "file", "files", "quality", "hindi", "english",
            "tamil", "telugu", "dubbed", "dual", "audio", "ok", "okay", "yaar",
            "yar", "dost", "friend", "group", "bot", "admin", "thanks", "thank",
            "thx", "tq", "good", "nice", "hello", "hi", "hey", "hii", "hlo",
            "kese", "kaise", "how", "are", "you", "kr", "kar", "dena",
            "mil", "mile", "mila", "available", "he", "h", "krdo", "kardo",
        }
        q_clean = re.sub(r"[^\w\s]", " ", (query or "").lower()).strip()
        raw_tokens = [t for t in q_clean.split() if len(t) > 0]
        q_tokens = [t for t in raw_tokens if t not in STOPWORDS and len(t) >= 2]
        if not q_tokens:
            q_tokens = [t for t in raw_tokens if len(t) >= 3]
        if not q_tokens:
            return []

        all_movies = list(self._mem_movies.values())
        if self.has_mongo:
            try:
                async for doc in self.movies_col.find({}):
                    mid = doc.get("movie_id")
                    if mid:
                        doc.pop("_id", None)
                        self._mem_movies[mid] = doc
                all_movies = list(self._mem_movies.values())
                try:
                    self._save_local_movies()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"[DB] Mongo search load: {e}")

        scored = []
        for m in all_movies:
            title = m.get("title", "") or ""
            year = str(m.get("year", "") or "")
            norm_title = m.get("normalized_title") or title.lower()
            searchable = f"{title.lower()} {norm_title} {year}"
            compact = re.sub(r"\s+", "", searchable)
            matched = 0.0
            for tok in q_tokens:
                if tok in searchable or tok in compact:
                    matched += 1
                else:
                    for word in searchable.split():
                        if word.startswith(tok) or tok.startswith(word):
                            matched += 0.5
                            break
            if matched <= 0:
                continue
            score = matched
            core_phrase = " ".join(q_tokens)
            if core_phrase in searchable:
                score += 5
            score += max(0, 3 - abs(len(title.split()) - len(q_tokens)) * 0.2)
            scored.append((score, m))

        scored.sort(key=lambda x: (-x[0], x[1].get("year") or 9999, x[1].get("title", "") or ""))
        return [m for _, m in scored]

    async def add_quality_to_movie(self, movie_id: str, quality: str, quality_data: dict) -> bool:
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id].setdefault("qualities", {})[quality] = quality_data
            self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
            self._save_local_movies()
            if self.has_mongo:
                try:
                    await self.movies_col.update_one(
                        {"movie_id": movie_id},
                        {"$set": {
                            f"qualities.{quality}": quality_data,
                            "updated_at": self._mem_movies[movie_id]["updated_at"],
                        }},
                    )
                except Exception as e:
                    logger.error(f"[DB] add_quality: {e}")
            return True
        return False

    async def rename_quality_key(self, movie_id: str, old_key: str, new_key: str) -> bool:
        if movie_id in self._mem_movies:
            qualities = self._mem_movies[movie_id].get("qualities", {})
            if old_key in qualities:
                qualities[new_key] = qualities.pop(old_key)
                self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
                self._save_local_movies()
                if self.has_mongo:
                    try:
                        await self.movies_col.update_one(
                            {"movie_id": movie_id},
                            {"$set": {"qualities": qualities, "updated_at": self._mem_movies[movie_id]["updated_at"]}},
                        )
                    except Exception as e:
                        logger.error(f"[DB] rename_quality: {e}")
                return True
        return False

    async def update_movie_poster(self, movie_id: str, poster_file_id: str, source: str = "manual") -> bool:
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id]["poster_file_id"] = poster_file_id
            self._mem_movies[movie_id]["poster_source"] = source
            self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
            self._save_local_movies()
            if self.has_mongo:
                try:
                    await self.movies_col.update_one(
                        {"movie_id": movie_id},
                        {"$set": {
                            "poster_file_id": poster_file_id,
                            "poster_source": source,
                            "updated_at": self._mem_movies[movie_id]["updated_at"],
                        }},
                    )
                except Exception as e:
                    logger.error(f"[DB] update_poster: {e}")
            return True
        return False

    async def delete_movie(self, movie_id: str) -> bool:
        if movie_id in self._mem_movies:
            del self._mem_movies[movie_id]
            self._save_local_movies()
        if self.has_mongo:
            try:
                await self.movies_col.delete_one({"movie_id": movie_id})
            except Exception as e:
                logger.error(f"[DB] delete_movie: {e}")
                return False
        return True

    async def list_movies(self, limit: int = 10, skip: int = 0):
        all_movies = list(self._mem_movies.values())
        return all_movies[skip:skip + limit]

    async def total_movies_count(self) -> int:
        return len(self._mem_movies)

    async def get_active_session(self, admin_id: int) -> dict:
        return self._mem_sessions.get(int(admin_id))

    async def set_active_session(self, admin_id: int, session_data: dict):
        self._mem_sessions[int(admin_id)] = session_data
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.update_one(
                    {"admin_id": int(admin_id)},
                    {"$set": {"admin_id": int(admin_id), "session": session_data}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"[DB] set_active_session: {e}")

    async def clear_active_session(self, admin_id: int):
        self._mem_sessions.pop(int(admin_id), None)
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.delete_one({"admin_id": int(admin_id)})
            except Exception as e:
                logger.warning(f"[DB] clear_active_session: {e}")

    async def get_all_active_sessions(self) -> list:
        return list(self._mem_sessions.values())

    async def sync_from_group_and_channel(self, bot, limit: int = 500):
        group_id = getattr(Config, "MOVIE_GROUP_ID", None)
        if not group_id:
            return 0
        count = 0
        try:
            async for msg in bot.get_chat_history(group_id, limit=limit):
                text = (msg.caption or msg.text or "")
                if "\ud83c\udfac" in text and "Available Downloads" in text:
                    already = any(m.get("group_msg_id") == msg.id for m in self._mem_movies.values())
                    if already:
                        continue
                    title_m = re.search(r"\ud83c\udfac\s*(?:<b>)?([^\n<(]+)(?:</b>)?", text)
                    if not title_m:
                        continue
                    raw_title = title_m.group(1).strip()
                    year_m = re.search(r"\((\d{4})\)", raw_title)
                    year = int(year_m.group(1)) if year_m else None
                    title = re.sub(r"\(\d{4}\)", "", raw_title).strip()
                    if not title:
                        continue
                    norm = re.sub(r"[^\w\s]", "", title.lower()).strip()
                    link = self.make_telegram_post_link(group_id, msg.id)
                    await self.create_movie(
                        title=title,
                        normalized_title=norm,
                        year=year,
                    )
                    # attach group post info to last created
                    for m_id, m in list(self._mem_movies.items()):
                        if m.get("title") == title and not m.get("group_msg_id"):
                            await self.update_movie_group_post(m_id, group_id, msg.id)
                            count += 1
                            break
            if count:
                logger.info(f"[DB] Synced {count} movies from group post history!")
        except Exception as e:
            logger.warning(f"[DB] Group sync notice: {e}")
        return count


db = Database(Config.DATABASE_URL, Config.DATABASE_NAME)
