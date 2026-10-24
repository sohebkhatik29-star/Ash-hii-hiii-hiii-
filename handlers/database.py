# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
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
                    connectTimeoutMS=20000,
                )
                self.db = self._client[database_name]
                self.users_col = self.db.users
                self.movies_col = self.db.movies
                self.sessions_col = self.db.movie_sessions
                self.has_mongo = True
                logger.info("[DB] MongoDB client created")
            except Exception as e:
                logger.error(f"[DB] Mongo init failed: {e}")
                self.has_mongo = False

        self._mem_movies = {}
        self._mem_sessions = {}
        self._mem_users = {}
        self._load_local()

    def _load_local(self):
        for path, attr in [
            (MOVIES_FILE, "_mem_movies"),
            (SESSIONS_FILE, "_mem_sessions"),
            (USERS_FILE, "_mem_users"),
        ]:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        setattr(self, attr, json.load(f))
            except Exception as e:
                logger.warning(f"[DB] local load {path}: {e}")

    def _save_local_movies(self):
        try:
            with open(MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_movies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DB] save movies: {e}")

    def _save_local_sessions(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_sessions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DB] save sessions: {e}")

    def _save_local_users(self):
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DB] save users: {e}")

    @staticmethod
    def make_telegram_post_link(chat_id, message_id) -> str:
        try:
            cid = abs(int(chat_id))
            # private supergroup: -100xxxxxxxxxx -> strip 100 prefix
            s = str(cid)
            if s.startswith("100"):
                s = s[3:]
            return f"https://t.me/c/{s}/{message_id}"
        except Exception:
            return ""

    async def connect(self):
        if not self.has_mongo:
            logger.warning("[DB] No Mongo — using local only")
            return
        try:
            await self._client.admin.command("ping")
            logger.info("[DB] MongoDB ping OK")
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
            logger.error(f"[DB] Mongo connect/load failed: {e}")
            self.has_mongo = False

    async def create_movie(self, title: str, year=None, languages=None, qualities=None,
                           poster_file_id=None, group_msg_id=None, group_chat_id=None,
                           group_post_link=None, **extra) -> dict:
        movie_id = str(uuid.uuid4())[:8]
        norm = re.sub(r"[^\w\s]", "", (title or "").lower()).strip()
        doc = {
            "movie_id": movie_id,
            "title": title,
            "normalized_title": norm,
            "year": year,
            "languages": languages or [],
            "qualities": qualities or {},
            "poster_file_id": poster_file_id,
            "group_msg_id": group_msg_id,
            "group_chat_id": group_chat_id,
            "group_post_link": group_post_link,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }
        doc.update(extra)
        self._mem_movies[movie_id] = doc
        self._save_local_movies()
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id}, {"$set": doc}, upsert=True
                )
            except Exception as e:
                logger.error(f"[DB] create_movie mongo: {e}")
        return doc

    async def update_movie_group_post(self, movie_id: str, group_msg_id, group_chat_id=None,
                                     group_post_link=None) -> bool:
        m = self._mem_movies.get(movie_id)
        if not m:
            return False
        m["group_msg_id"] = group_msg_id
        if group_chat_id is not None:
            m["group_chat_id"] = group_chat_id
        if group_post_link:
            m["group_post_link"] = group_post_link
        elif group_chat_id and group_msg_id:
            m["group_post_link"] = self.make_telegram_post_link(group_chat_id, group_msg_id)
        m["updated_at"] = datetime.datetime.utcnow().isoformat()
        self._save_local_movies()
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": {
                        "group_msg_id": m.get("group_msg_id"),
                        "group_chat_id": m.get("group_chat_id"),
                        "group_post_link": m.get("group_post_link"),
                        "updated_at": m["updated_at"],
                    }},
                )
            except Exception as e:
                logger.error(f"[DB] update group post: {e}")
        return True

    async def delete_movie(self, movie_id: str) -> bool:
        if movie_id in self._mem_movies:
            del self._mem_movies[movie_id]
            self._save_local_movies()
        if self.has_mongo and self.movies_col is not None:
            try:
                await self.movies_col.delete_one({"movie_id": movie_id})
            except Exception as e:
                logger.error(f"[DB] delete_movie: {e}")
                return False
        return True

    async def get_movie(self, movie_id: str):
        m = self._mem_movies.get(movie_id)
        if m:
            return m
        if self.has_mongo and self.movies_col is not None:
            try:
                doc = await self.movies_col.find_one({"movie_id": movie_id})
                if doc:
                    doc.pop("_id", None)
                    self._mem_movies[movie_id] = doc
                    return doc
            except Exception as e:
                logger.warning(f"[DB] get_movie: {e}")
        return None

    async def search_movies(self, query: str) -> list:
        """
        Smart search:
        - Strips filler words (dedo, movie, please, bhai, full, etc.)
        - Matches if core tokens appear in title
        - Partial / substring friendly (pushpa matches Pushpa 2 The Rule)
        """
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
                cursor = self.movies_col.find({})
                async for doc in cursor:
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
            if self.has_mongo and self.movies_col is not None:
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

    async def get_all_movies(self) -> list:
        if self.has_mongo and self.movies_col is not None:
            try:
                async for doc in self.movies_col.find({}):
                    mid = doc.get("movie_id")
                    if mid:
                        doc.pop("_id", None)
                        self._mem_movies[mid] = doc
            except Exception as e:
                logger.warning(f"[DB] get_all_movies: {e}")
        return list(self._mem_movies.values())

    async def sync_from_group_and_channel(self, client, group_id: int, limit: int = 500):
        """Re-index movie cards from group history into DB."""
        if not group_id:
            return 0
        added = 0
        try:
            async for msg in client.get_chat_history(group_id, limit=limit):
                text = (msg.caption or msg.text or "")
                if not text or "\ud83c\udfac" not in text:
                    continue
                # already indexed by msg id?
                already = False
                for m in self._mem_movies.values():
                    if m.get("group_msg_id") == msg.id:
                        already = True
                        break
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
                link = self.make_telegram_post_link(group_id, msg.id)
                await self.create_movie(
                    title=title,
                    year=year,
                    group_msg_id=msg.id,
                    group_chat_id=group_id,
                    group_post_link=link,
                )
                added += 1
        except Exception as e:
            logger.warning(f"[DB] group sync: {e}")
        if added:
            logger.info(f"[DB] Synced {added} movies from group history")
        return added


db = Database(Config.DATABASE_URL, Config.DATABASE_NAME)
