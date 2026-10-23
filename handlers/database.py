# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/database.py
# Purpose: Dual-Persistent Database Engine (MongoDB Atlas + Local Safe Storage)
#          Ensures zero data loss, survives restarts, and never fails links.
# ==============================================================================

import os
import json
import uuid
import datetime
import logging
import asyncio
import re
import motor.motor_asyncio
from configs import Config

logger = logging.getLogger(__name__)

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
        # 1. MongoDB Setup with fast timeout
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
                    serverSelectionTimeoutMS=3000,
                    connectTimeoutMS=3000
                )
                self.db = self._client[database_name]
                self.users_col = self.db.users
                self.movies_col = self.db.movies
                self.sessions_col = self.db.movie_sessions
                self.has_mongo = True
            except Exception as e:
                logger.warning(f"[DB] MongoDB init warning: {e}")

        # 2. In-Memory & Local Disk Persistence
        self._mem_movies = {}
        self._mem_sessions = {}
        self._mem_users = {}
        self._load_local()

    def _load_local(self):
        for path, store in [
            (MOVIES_FILE, self._mem_movies),
            (SESSIONS_FILE, self._mem_sessions),
            (USERS_FILE, self._mem_users),
        ]:
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            store.update(data)
            except Exception as e:
                logger.warning(f"[DB] Local load warning {path}: {e}")

    def _save_local_movies(self):
        try:
            with open(MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_movies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DB] Save movies failed: {e}")

    def _save_local_sessions(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_sessions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DB] Save sessions failed: {e}")

    def _save_local_users(self):
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[DB] Save users failed: {e}")

    @staticmethod
    def make_telegram_post_link(chat_id, message_id) -> str:
        """Private group/supergroup post link: https://t.me/c/{id}/{msg}"""
        try:
            cid = int(chat_id)
            # Telegram private chat links drop the -100 prefix
            if cid < 0:
                cid = abs(cid)
                if str(cid).startswith("100"):
                    cid = int(str(cid)[3:])
            return f"https://t.me/c/{cid}/{int(message_id)}"
        except Exception:
            return f"https://t.me/c/{chat_id}/{message_id}"

    async def create_movie(self, title: str, normalized_title: str, year: int = None,
                           languages: list = None, qualities: dict = None,
                           poster_file_id: str = None, group_chat_id=None,
                           group_msg_id=None, group_post_link: str = None,
                           movie_id: str = None) -> dict:
        movie_id = movie_id or uuid.uuid4().hex[:8]
        now = datetime.datetime.utcnow().isoformat()
        doc = {
            "movie_id": movie_id,
            "title": title,
            "normalized_title": normalized_title or title.lower(),
            "year": year,
            "languages": languages or [],
            "qualities": qualities or {},
            "poster_file_id": poster_file_id,
            "group_chat_id": group_chat_id,
            "group_msg_id": group_msg_id,
            "group_post_link": group_post_link,
            "created_at": now,
            "updated_at": now,
        }
        self._mem_movies[movie_id] = doc
        self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": doc},
                    upsert=True
                )
            except Exception as e:
                logger.error(f"[DB] Mongo create_movie: {e}")
        return doc

    async def update_movie(self, movie_id: str, **fields) -> bool:
        if movie_id not in self._mem_movies:
            return False
        fields["updated_at"] = datetime.datetime.utcnow().isoformat()
        self._mem_movies[movie_id].update(fields)
        self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$set": fields}
                )
            except Exception as e:
                logger.error(f"[DB] Mongo update_movie: {e}")
        return True

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
            except Exception:
                pass
        return None

    async def find_movie_by_title(self, normalized_title: str, year: int = None) -> dict:
        for m in self._mem_movies.values():
            if m.get("normalized_title") == normalized_title:
                if year is None or m.get("year") == year:
                    return m
        if self.has_mongo:
            try:
                query = {"normalized_title": normalized_title}
                if year:
                    query["year"] = year
                doc = await self.movies_col.find_one(query)
                if doc:
                    doc.pop("_id", None)
                    self._mem_movies[doc["movie_id"]] = doc
                    return doc
            except Exception:
                pass
        return None

    async def search_movies(self, query: str) -> list:
        q_clean = re.sub(r"[^\w\s]", " ", query.lower()).strip()
        q_tokens = [t for t in q_clean.split() if len(t) > 0]
        if not q_tokens:
            return []

        all_movies = list(self._mem_movies.values())
        if self.has_mongo and len(all_movies) < 5:
            try:
                cursor = self.movies_col.find({})
                async for doc in cursor:
                    if doc.get("movie_id") not in self._mem_movies:
                        self._mem_movies[doc["movie_id"]] = doc
                all_movies = list(self._mem_movies.values())
            except Exception:
                pass

        results = []
        for m in all_movies:
            title = m.get("title", "")
            year = str(m.get("year", ""))
            norm_title = m.get("normalized_title", title.lower())
            searchable = f"{title.lower()} {norm_title} {year}"

            all_match = True
            for tok in q_tokens:
                if tok not in searchable:
                    all_match = False
                    break
            if all_match:
                results.append(m)

        # Oldest year first (Pushpa 2021 above Pushpa 2 2025), regardless of post order
        results.sort(key=lambda x: (x.get("year") or 9999, x.get("title", "") or ""))
        return results

    async def add_quality_to_movie(self, movie_id: str, quality: str, quality_data: dict) -> bool:
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id].setdefault("qualities", {})[quality] = quality_data
            self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
            self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {
                        "$set": {
                            f"qualities.{quality}": quality_data,
                            "updated_at": datetime.datetime.utcnow().isoformat()
                        }
                    }
                )
            except Exception as e:
                logger.error(f"[DB] add_quality: {e}")
                return False
        return True

    async def delete_movie(self, movie_id: str) -> bool:
        self._mem_movies.pop(movie_id, None)
        self._save_local_movies()
        if self.has_mongo:
            try:
                await self.movies_col.delete_one({"movie_id": movie_id})
            except Exception as e:
                logger.error(f"[DB] delete_movie: {e}")
                return False
        return True

    async def list_movies(self, limit: int = 50) -> list:
        all_movies = list(self._mem_movies.values())
        all_movies.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return all_movies[:limit]

    async def count_movies(self) -> int:
        return len(self._mem_movies)

    # ---- Users ----
    async def add_user(self, user_id: int, username: str = None):
        uid = str(user_id)
        self._mem_users[uid] = {
            "user_id": user_id,
            "username": username,
            "joined_at": datetime.datetime.utcnow().isoformat()
        }
        self._save_local_users()
        if self.has_mongo:
            try:
                await self.users_col.update_one(
                    {"user_id": user_id},
                    {"$set": self._mem_users[uid]},
                    upsert=True
                )
            except Exception:
                pass

    async def get_all_user_ids(self) -> list:
        return [int(uid) for uid in self._mem_users.keys()]

    # ---- Sessions (addmovie flow) ----
    async def save_session(self, user_id: int, session: dict):
        uid = str(user_id)
        self._mem_sessions[uid] = session
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.update_one(
                    {"user_id": user_id},
                    {"$set": {"user_id": user_id, **session}},
                    upsert=True
                )
            except Exception:
                pass

    async def get_session(self, user_id: int) -> dict:
        uid = str(user_id)
        if uid in self._mem_sessions:
            return self._mem_sessions[uid]
        if self.has_mongo:
            try:
                doc = await self.sessions_col.find_one({"user_id": user_id})
                if doc:
                    doc.pop("_id", None)
                    self._mem_sessions[uid] = doc
                    return doc
            except Exception:
                pass
        return None

    async def clear_session(self, user_id: int):
        uid = str(user_id)
        self._mem_sessions.pop(uid, None)
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.delete_one({"user_id": user_id})
            except Exception:
                pass

    async def import_from_channel_messages(self, messages: list):
        """Optional bulk import helper."""
        imported = 0
        for msg in messages:
            try:
                caption = msg.get("caption") or msg.get("text") or ""
                full_str = caption
                year_match = re.search(r"\((\d{4})\)", full_str)
                year = int(year_match.group(1)) if year_match else None
                title = re.sub(r"\(\d{4}\)", "", full_str).strip()
                title = title.split("\n")[0].strip()[:80] or "Untitled"
                norm = re.sub(r"[^\w\s]", "", title.lower()).strip()
                if not norm:
                    continue
                existing = await self.find_movie_by_title(norm, year)
                if not existing:
                    await self.create_movie(
                        title=title,
                        normalized_title=norm,
                        year=year,
                        languages=[],
                        qualities={},
                    )
                    imported += 1
            except Exception:
                continue
        return imported


# Global instance
db = Database(Config.DATABASE_URI if hasattr(Config, "DATABASE_URI") else getattr(Config, "MONGO_URI", None),
              getattr(Config, "DATABASE_NAME", "ash_movies"))
