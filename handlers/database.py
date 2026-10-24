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
                    self._mem_sessions = {int(k): v for k, v in data.items() if k.isdigit()}
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    self._mem_users = set(json.load(f))
            logger.info(
                f"[DB] Loaded {len(self._mem_movies)} movies & {len(self._mem_sessions)} sessions from local storage."
            )
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
            logger.warning("[DB] MongoDB NOT connected - filters will be lost on redeploy!")
            return
        try:
            await self._client.admin.command("ping")
            logger.info("[DB] MongoDB ping OK")
        except Exception as e:
            logger.error(f"[DB] MongoDB ping FAILED: {e}")
            self.has_mongo = False
            return
        try:
            await self.users_col.create_index("id", unique=True)
            await self.movies_col.create_index("movie_id", unique=True)
            await self.movies_col.create_index("normalized_title")
            await self.sessions_col.create_index("admin_id", unique=True)
            logger.info("[DB] MongoDB indexes checked successfully!")
        except Exception as e:
            logger.warning(f"[DB] Index creation skipped: {e}")
        try:
            n = 0
            async for doc in self.movies_col.find({}):
                mid = doc.get("movie_id")
                if mid:
                    doc.pop("_id", None)
                    self._mem_movies[mid] = doc
                    n += 1
            if n:
                self._save_local_movies()
            logger.info(
                f"[DB] Loaded {n} movies from MongoDB (total in memory={len(self._mem_movies)})"
            )
        except Exception as e:
            logger.error(f"[DB] Failed to load movies from Mongo: {e}")

    async def add_user(self, id: int):
        self._mem_users.add(int(id))
        self._save_local_users()
        if self.has_mongo:
            try:
                await self.users_col.update_one(
                    {"id": int(id)},
                    {
                        "$setOnInsert": {
                            "id": int(id),
                            "join_date": datetime.date.today().isoformat(),
                            "ban_status": {
                                "is_banned": False,
                                "ban_duration": 0,
                                "banned_on": datetime.date.max.isoformat(),
                                "ban_reason": "",
                            },
                        }
                    },
                    upsert=True,
                )
            except Exception:
                pass

    async def is_user_exist(self, id: int) -> bool:
        if int(id) in self._mem_users:
            return True
        if self.has_mongo:
            try:
                user = await self.users_col.find_one({"id": int(id)})
                if user:
                    self._mem_users.add(int(id))
                    return True
            except Exception:
                pass
        return False

    async def total_users_count(self) -> int:
        return len(self._mem_users) or 1

    async def get_ban_status(self, id: int):
        return {"is_banned": False}

    def generate_movie_id(self) -> str:
        return uuid.uuid4().hex[:8]

    async def create_movie(
        self,
        title: str,
        normalized_title: str,
        year: int = None,
        languages: list = None,
        poster_file_id: str = None,
        admin_id: int = None,
        movie_id: str = None,
    ) -> dict:
        m_id = movie_id or self.generate_movie_id()
        movie_doc = {
            "movie_id": m_id,
            "title": title,
            "normalized_title": normalized_title,
            "year": year,
            "languages": languages or ["Hindi"],
            "poster_file_id": poster_file_id,
            "poster_source": "extracted" if poster_file_id else "none",
            "created_by": admin_id,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
            "qualities": {},
        }
        self._mem_movies[m_id] = movie_doc
        self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": m_id},
                    {"$set": movie_doc},
                    upsert=True,
                )
                logger.info(f"[DB] Movie saved to Mongo: {title} ({m_id})")
            except Exception as e:
                logger.error(f"[DB] Mongo create movie error: {e}")

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
                    self._save_local_movies()
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

    def make_telegram_post_link(self, chat_id: int, msg_id: int) -> str:
        clean = str(chat_id).replace("-100", "").replace("-", "")
        return f"https://t.me/c/{clean}/{msg_id}"

    async def update_movie_group_post(self, movie_id: str, group_chat_id: int, group_msg_id: int):
        post_link = self.make_telegram_post_link(group_chat_id, group_msg_id)
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id]["group_chat_id"] = group_chat_id
            self._mem_movies[movie_id]["group_msg_id"] = group_msg_id
            self._mem_movies[movie_id]["group_post_link"] = post_link
            self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {
                        "$set": {
                            "group_chat_id": group_chat_id,
                            "group_msg_id": group_msg_id,
                            "group_post_link": post_link,
                        }
                    },
                )
            except Exception:
                pass
        return post_link

    async def search_movies(self, query: str) -> list:
        q_clean = re.sub(r"[^\w\s]", " ", query.lower()).strip()
        q_tokens = [t for t in q_clean.split() if len(t) > 0]
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

        results = []
        for m in all_movies:
            title = m.get("title", "")
            year = str(m.get("year", "") or "")
            norm_title = m.get("normalized_title", title.lower())
            searchable = f"{title.lower()} {norm_title} {year}"

            all_match = True
            for tok in q_tokens:
                if tok not in searchable:
                    all_match = False
                    break
            if all_match:
                results.append(m)

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
                            "updated_at": datetime.datetime.utcnow().isoformat(),
                        }
                    },
                )
            except Exception:
                pass
        return True

    async def rename_quality_key(self, movie_id: str, old_key: str, new_key: str) -> bool:
        if movie_id in self._mem_movies:
            qualities = self._mem_movies[movie_id].get("qualities", {})
            if old_key in qualities:
                qualities[new_key] = qualities.pop(old_key)
                self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {"$rename": {f"qualities.{old_key}": f"qualities.{new_key}"}},
                )
            except Exception:
                pass
        return True

    async def update_movie_poster(self, movie_id: str, poster_file_id: str, source: str = "manual") -> bool:
        if movie_id in self._mem_movies:
            self._mem_movies[movie_id]["poster_file_id"] = poster_file_id
            self._mem_movies[movie_id]["poster_source"] = source
            self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": movie_id},
                    {
                        "$set": {
                            "poster_file_id": poster_file_id,
                            "poster_source": source,
                            "updated_at": datetime.datetime.utcnow().isoformat(),
                        }
                    },
                )
            except Exception:
                pass
        return True

    async def delete_movie(self, movie_id: str) -> bool:
        if movie_id in self._mem_movies:
            del self._mem_movies[movie_id]
            self._save_local_movies()

        if self.has_mongo:
            try:
                await self.movies_col.delete_one({"movie_id": movie_id})
            except Exception:
                pass
        return True

    async def set_active_session(self, admin_id: int, session: dict):
        self._mem_sessions[int(admin_id)] = session
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.update_one(
                    {"admin_id": int(admin_id)},
                    {"$set": session},
                    upsert=True,
                )
            except Exception:
                pass

    async def get_active_session(self, admin_id: int) -> dict:
        if int(admin_id) in self._mem_sessions:
            return self._mem_sessions[int(admin_id)]
        if self.has_mongo:
            try:
                doc = await self.sessions_col.find_one({"admin_id": int(admin_id)})
                if doc:
                    doc.pop("_id", None)
                    self._mem_sessions[int(admin_id)] = doc
                    return doc
            except Exception:
                pass
        return None

    async def clear_active_session(self, admin_id: int):
        if int(admin_id) in self._mem_sessions:
            del self._mem_sessions[int(admin_id)]
            self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.delete_one({"admin_id": int(admin_id)})
            except Exception:
                pass

    async def get_all_active_sessions(self) -> list:
        return list(self._mem_sessions.values())

    async def sync_from_group_and_channel(self, bot):
        from configs import Config

        if not Config.MOVIE_GROUP_ID:
            return

        try:
            count = 0
            async for msg in bot.get_chat_history(Config.MOVIE_GROUP_ID, limit=500):
                text = msg.caption or msg.text or ""
                if "\U0001f3ac" in text and "Available Downloads" in text:
                    title_match = re.search(r"\U0001f3ac\s*(?:<b>)?([^\n<]+)(?:</b>)?", text)
                    if title_match:
                        full_str = title_match.group(1).strip()
                        year_match = re.search(r"\((\d{4})\)", full_str)
                        year = int(year_match.group(1)) if year_match else None
                        title = re.sub(r"\(\d{4}\)", "", full_str).strip()

                        clean_chat = str(Config.MOVIE_GROUP_ID).replace("-100", "").replace("-", "")
                        post_link = f"https://t.me/c/{clean_chat}/{msg.id}"

                        found = False
                        for m_id, m in self._mem_movies.items():
                            if m.get("title", "").lower() == title.lower():
                                m["group_msg_id"] = msg.id
                                m["group_post_link"] = post_link
                                found = True
                                break

                        if not found:
                            m_id = self.generate_movie_id()
                            doc = {
                                "movie_id": m_id,
                                "title": title,
                                "normalized_title": title.lower().strip(),
                                "year": year,
                                "group_chat_id": Config.MOVIE_GROUP_ID,
                                "group_msg_id": msg.id,
                                "group_post_link": post_link,
                                "qualities": {},
                                "languages": ["Hindi"],
                            }
                            self._mem_movies[m_id] = doc
                            count += 1
                            if self.has_mongo:
                                try:
                                    await self.movies_col.update_one(
                                        {"movie_id": m_id}, {"$set": doc}, upsert=True
                                    )
                                except Exception:
                                    pass
                        elif self.has_mongo:
                            try:
                                await self.movies_col.update_one(
                                    {"title": title},
                                    {
                                        "$set": {
                                            "group_msg_id": msg.id,
                                            "group_post_link": post_link,
                                            "group_chat_id": Config.MOVIE_GROUP_ID,
                                        }
                                    },
                                )
                            except Exception:
                                pass

            self._save_local_movies()
            logger.info(
                f"[DB] Group sync done - new={count}, total_in_memory={len(self._mem_movies)}"
            )
        except Exception as e:
            logger.warning(f"[DB] Group sync notice: {e}")


db = Database(Config.DATABASE_URL, Config.DATABASE_NAME)
