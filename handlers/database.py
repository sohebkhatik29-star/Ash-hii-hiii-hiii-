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
                    raw = json.load(f)
                    self._mem_sessions = {int(k): v for k, v in raw.items()}
            if os.path.exists(USERS_FILE):
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    self._mem_users = set(json.load(f))
        except Exception as e:
            logger.warning(f"[DB] Local load notice: {e}")

    def _save_local_movies(self):
        try:
            with open(MOVIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._mem_movies, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DB] Local movies save: {e}")

    def _save_local_sessions(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in self._mem_sessions.items()}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[DB] Local sessions save: {e}")

    def _save_local_users(self):
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self._mem_users), f)
        except Exception as e:
            logger.warning(f"[DB] Local users save: {e}")

    async def init_indexes(self):
        if not self.has_mongo:
            return
        try:
            await self.movies_col.create_index("movie_id", unique=True)
            await self.movies_col.create_index("normalized_title")
            await self.movies_col.create_index("group_msg_id")
            await self.users_col.create_index("user_id", unique=True)
            await self.sessions_col.create_index("admin_id", unique=True)
            logger.info("[DB] Indexes ready")
        except Exception as e:
            logger.warning(f"[DB] Index notice: {e}")

    async def add_user(self, user_id: int):
        self._mem_users.add(int(user_id))
        self._save_local_users()
        if self.has_mongo:
            try:
                await self.users_col.update_one(
                    {"user_id": int(user_id)},
                    {"$set": {"user_id": int(user_id), "joined_at": datetime.datetime.utcnow().isoformat()}},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"[DB] add_user: {e}")

    async def total_users_count(self) -> int:
        if self.has_mongo:
            try:
                return await self.users_col.count_documents({})
            except Exception:
                pass
        return len(self._mem_users)

    async def get_all_users(self) -> list:
        if self.has_mongo:
            try:
                return [doc["user_id"] async for doc in self.users_col.find({}, {"user_id": 1})]
            except Exception:
                pass
        return list(self._mem_users)

    async def ban_user(self, user_id: int):
        pass

    async def create_movie(self, title: str, normalized_title: str, year: int = None,
                           movie_id: str = None, poster_file_id: str = None,
                           qualities: dict = None) -> str:
        mid = movie_id or str(uuid.uuid4())
        doc = {
            "movie_id": mid,
            "title": title,
            "normalized_title": normalized_title,
            "year": year,
            "poster_file_id": poster_file_id,
            "qualities": qualities or {},
            "group_chat_id": None,
            "group_msg_id": None,
            "group_post_link": None,
            "created_at": datetime.datetime.utcnow().isoformat(),
            "updated_at": datetime.datetime.utcnow().isoformat(),
        }
        self._mem_movies[mid] = doc
        self._save_local_movies()
        if self.has_mongo:
            try:
                await self.movies_col.update_one(
                    {"movie_id": mid},
                    {"$set": doc},
                    upsert=True,
                )
            except Exception as e:
                logger.error(f"[DB] create_movie: {e}")
        return mid

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
                query = {"normalized_title": normalized_title}
                if year is not None:
                    query["year"] = year
                doc = await self.movies_col.find_one(query)
                if doc:
                    doc.pop("_id", None)
                    self._mem_movies[doc["movie_id"]] = doc
                    return doc
            except Exception as e:
                logger.warning(f"[DB] find_movie_by_title: {e}")
        return None

    def make_telegram_post_link(self, chat_id, msg_id) -> str:
        chat_str = str(chat_id)
        if chat_str.startswith("-100"):
            internal = chat_str[4:]
        elif chat_str.startswith("-"):
            internal = chat_str[1:]
        else:
            internal = chat_str
        return f"https://t.me/c/{internal}/{msg_id}"

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
        """Tight token match: require majority of title words; year only exact-boosts."""
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

        # Separate pure year tokens (4 digits 19xx/20xx) from title tokens
        year_tokens = set()
        for t in q_tokens:
            if t.isdigit() and len(t) == 4 and t.startswith(("19", "20")):
                year_tokens.add(t)
        title_tokens = [t for t in q_tokens if t not in year_tokens]
        # If user only typed a year, fall back to year search
        if not title_tokens and year_tokens:
            title_tokens = list(year_tokens)
            year_tokens = set()

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
            norm_title = (m.get("normalized_title") or title).lower()
            title_searchable = f"{title.lower()} {norm_title}"
            compact_title = re.sub(r"\s+", "", title_searchable)

            title_matched = 0.0
            for tok in title_tokens:
                if tok in title_searchable or tok in compact_title:
                    title_matched += 1.0
                else:
                    for word in title_searchable.split():
                        if len(word) >= 3 and len(tok) >= 3 and (word.startswith(tok) or tok.startswith(word)):
                            title_matched += 0.5
                            break

            # When multiple title tokens exist, require majority match
            if len(title_tokens) >= 2 and title_matched < max(1.0, len(title_tokens) * 0.5):
                continue
            if title_matched <= 0:
                continue

            score = float(title_matched)
            # Exact year boost only (never treat year as title word for unrelated films)
            if year and year in year_tokens:
                score += 2.0
            core_phrase = " ".join(title_tokens)
            if core_phrase and core_phrase in title_searchable:
                score += 5
            score += max(0, 3 - abs(len(title.split()) - len(title_tokens)) * 0.2)
            scored.append((score, m))

        scored = [(s, m) for s, m in scored if s >= 1]
        # Oldest year first when scores equal
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
            self._mem_movies[movie_id]["updated_at"] = datetime.datetime.utcnow().isoformat()
            self._save_local_movies()
            if self.has_mongo:
                try:
                    await self.movies_col.update_one(
                        {"movie_id": movie_id},
                        {"$set": {
                            "poster_file_id": poster_file_id,
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

    async def delete_by_group_msg_id(self, group_msg_id) -> list:
        """Delete all movies that reference this group message id. Returns deleted titles."""
        try:
            target = int(group_msg_id)
        except (TypeError, ValueError):
            return []
        deleted_titles = []
        to_remove = []
        for mid, m in list(self._mem_movies.items()):
            gmid = m.get("group_msg_id")
            link = str(m.get("group_post_link") or "")
            try:
                if gmid is not None and int(gmid) == target:
                    to_remove.append(mid)
                    deleted_titles.append(m.get("title", "?"))
                elif link and link.rstrip("/").endswith(f"/{target}"):
                    to_remove.append(mid)
                    deleted_titles.append(m.get("title", "?"))
            except (TypeError, ValueError):
                continue
        for mid in set(to_remove):
            await self.delete_movie(mid)
        # Always also query mongo in case mem is stale
        if self.has_mongo:
            try:
                cursor = self.movies_col.find({
                    "$or": [
                        {"group_msg_id": target},
                        {"group_msg_id": str(target)},
                        {"group_post_link": {"$regex": f"/{target}$"}},
                    ]
                })
                async for doc in cursor:
                    mid = doc.get("movie_id")
                    if mid and mid not in to_remove:
                        await self.delete_movie(mid)
                        deleted_titles.append(doc.get("title", "?"))
            except Exception as e:
                logger.warning(f"[DB] delete_by_group_msg_id mongo: {e}")
        if deleted_titles:
            logger.info(f"[DB] delete_by_group_msg_id({target}) → {deleted_titles}")
        return deleted_titles

    async def list_movies(self, limit: int = 10, skip: int = 0):
        all_movies = list(self._mem_movies.values())
        return all_movies[skip:skip + limit]

    async def total_movies_count(self) -> int:
        return len(self._mem_movies)

    async def get_active_session(self, admin_id: int) -> dict:
        sid = int(admin_id)
        if sid in self._mem_sessions:
            return self._mem_sessions[sid]
        if self.has_mongo:
            try:
                doc = await self.sessions_col.find_one({"admin_id": sid})
                if doc:
                    doc.pop("_id", None)
                    self._mem_sessions[sid] = doc
                    return doc
            except Exception as e:
                logger.warning(f"[DB] get_active_session: {e}")
        return None

    async def set_active_session(self, admin_id: int, session: dict):
        sid = int(admin_id)
        session["admin_id"] = sid
        self._mem_sessions[sid] = session
        self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.update_one(
                    {"admin_id": sid},
                    {"$set": session},
                    upsert=True,
                )
            except Exception as e:
                logger.warning(f"[DB] set_active_session: {e}")

    async def clear_active_session(self, admin_id: int):
        sid = int(admin_id)
        if sid in self._mem_sessions:
            del self._mem_sessions[sid]
            self._save_local_sessions()
        if self.has_mongo:
            try:
                await self.sessions_col.delete_one({"admin_id": sid})
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
                    await self.create_movie(title=title, normalized_title=norm, year=year)
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
