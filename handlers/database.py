# Runtime restore of database.py — Render-safe + Mongo always-sync + year ASC
import importlib.util
import urllib.request
import tempfile
import sys
import os
import re

_URL = (
    "https://raw.githubusercontent.com/sohebkhatik29-star/Ash-hii-hiii-hiii-/"
    "7aebbab5eaeda8fefc2a9ae4158b50592d2d8369/handlers/database.py"
)
_path = os.path.join(tempfile.gettempdir(), "ash_database_restored.py")


def _load():
    with urllib.request.urlopen(_URL, timeout=30) as r:
        text = r.read().decode("utf-8")

    if "import re" not in text:
        text = text.replace("import asyncio\n", "import asyncio\nimport re\n")

    # --- DATA_DIR: never fail on Render read-only root ---
    old_data = (
        'DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")\n'
        "os.makedirs(DATA_DIR, exist_ok=True)"
    )
    new_data = (
        "_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        'DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(_BASE, "data")\n'
        "try:\n"
        "    os.makedirs(DATA_DIR, exist_ok=True)\n"
        "except OSError:\n"
        '    DATA_DIR = "/tmp/ash_movie_data"\n'
        "    os.makedirs(DATA_DIR, exist_ok=True)"
    )
    if old_data in text:
        text = text.replace(old_data, new_data)
    else:
        text = text.replace(
            "os.makedirs(DATA_DIR, exist_ok=True)",
            "try:\n    os.makedirs(DATA_DIR, exist_ok=True)\nexcept OSError:\n"
            "    DATA_DIR = '/tmp/ash_movie_data'\n"
            "    os.makedirs(DATA_DIR, exist_ok=True)",
            1,
        )

    # --- Longer Mongo timeouts so Render can connect ---
    text = text.replace(
        "serverSelectionTimeoutMS=3000,\n                    connectTimeoutMS=3000",
        "serverSelectionTimeoutMS=15000,\n                    connectTimeoutMS=15000",
    )

    # --- Year ASC sort (oldest first) ---
    old_sort = 'results.sort(key=lambda x: (x.get("year") or 0, x.get("title", "")), reverse=True)'
    new_sort = 'results.sort(key=lambda x: (x.get("year") or 9999, x.get("title", "") or ""))'
    if old_sort in text:
        text = text.replace(old_sort, new_sort)

    # --- search_movies: ALWAYS load from Mongo (not only when < 5) ---
    old_search_if = "        all_movies = list(self._mem_movies.values())\n        if self.has_mongo and len(all_movies) < 5:"
    new_search_if = "        all_movies = list(self._mem_movies.values())\n        if self.has_mongo:"
    if old_search_if in text:
        text = text.replace(old_search_if, new_search_if)

    # --- After loading from Mongo in search, also save local so next boot is fast ---
    old_reload = (
        "                async for doc in cursor:\n"
        "                    if doc.get(\"movie_id\") not in self._mem_movies:\n"
        "                        self._mem_movies[doc[\"movie_id\"]] = doc\n"
        "                all_movies = list(self._mem_movies.values())\n"
        "            except Exception:\n"
        "                pass"
    )
    new_reload = (
        "                async for doc in cursor:\n"
        "                    mid = doc.get(\"movie_id\")\n"
        "                    if mid:\n"
        "                        self._mem_movies[mid] = doc\n"
        "                all_movies = list(self._mem_movies.values())\n"
        "                try:\n"
        "                    self._save_local_movies()\n"
        "                except Exception:\n"
        "                    pass\n"
        "            except Exception as e:\n"
        "                logger.warning(f\"[DB] Mongo search load: {e}\")"
    )
    if old_reload in text:
        text = text.replace(old_reload, new_reload)

    # --- Group sync: higher limit + always write to Mongo ---
    old_sync = 'async for msg in bot.get_chat_history(Config.MOVIE_GROUP_ID, limit=100):'
    new_sync = 'async for msg in bot.get_chat_history(Config.MOVIE_GROUP_ID, limit=500):'
    if old_sync in text:
        text = text.replace(old_sync, new_sync)

    # After creating a movie from group sync, also insert into Mongo
    old_found_block = (
        "                        if not found:\n"
        "                            m_id = self.generate_movie_id()\n"
        "                            self._mem_movies[m_id] = {\n"
        "                                \"movie_id\": m_id,\n"
        "                                \"title\": title,\n"
        "                                \"normalized_title\": title.lower(),\n"
        "                                \"year\": year,\n"
        "                                \"group_chat_id\": Config.MOVIE_GROUP_ID,\n"
        "                                \"group_msg_id\": msg.id,\n"
        "                                \"group_post_link\": post_link,\n"
        "                                \"qualities\": {}\n"
        "                            }\n"
        "                            count += 1\n\n"
        "            if count > 0:\n"
        "                self._save_local_movies()\n"
        "                logger.info(f\"[DB] Synced {count} movies from group post history!\")"
    )
    new_found_block = (
        "                        if not found:\n"
        "                            m_id = self.generate_movie_id()\n"
        "                            doc = {\n"
        "                                \"movie_id\": m_id,\n"
        "                                \"title\": title,\n"
        "                                \"normalized_title\": title.lower(),\n"
        "                                \"year\": year,\n"
        "                                \"group_chat_id\": Config.MOVIE_GROUP_ID,\n"
        "                                \"group_msg_id\": msg.id,\n"
        "                                \"group_post_link\": post_link,\n"
        "                                \"qualities\": {},\n"
        "                                \"languages\": [\"Hindi\"]\n"
        "                            }\n"
        "                            self._mem_movies[m_id] = doc\n"
        "                            count += 1\n"
        "                            if self.has_mongo:\n"
        "                                try:\n"
        "                                    await self.movies_col.update_one(\n"
        "                                        {\"movie_id\": m_id},\n"
        "                                        {\"$set\": doc},\n"
        "                                        upsert=True\n"
        "                                    )\n"
        "                                except Exception:\n"
        "                                    pass\n"
        "                        elif self.has_mongo:\n"
        "                            try:\n"
        "                                await self.movies_col.update_one(\n"
        "                                    {\"title\": title},\n"
        "                                    {\"$set\": {\"group_msg_id\": msg.id, \"group_post_link\": post_link}},\n"
        "                                )\n"
        "                            except Exception:\n"
        "                                pass\n\n"
        "            # Always persist local cache after sync\n"
        "            self._save_local_movies()\n"
        "            if count > 0:\n"
        "                logger.info(f\"[DB] Synced {count} NEW movies from group history!\")\n"
        "            else:\n"
        "                logger.info(f\"[DB] Group sync done. Total movies in memory: {len(self._mem_movies)}\")"
    )
    if old_found_block in text:
        text = text.replace(old_found_block, new_found_block)

    # --- Also load ALL movies from Mongo inside init_indexes ---
    old_init = (
        "    async def init_indexes(self):\n"
        "        if not self.has_mongo:\n"
        "            return\n"
        "        try:\n"
        "            await self.users_col.create_index(\"id\", unique=True)\n"
        "            await self.movies_col.create_index(\"movie_id\", unique=True)\n"
        "            await self.movies_col.create_index(\"normalized_title\")\n"
        "            await self.sessions_col.create_index(\"admin_id\", unique=True)\n"
        "            logger.info(\"[DB] MongoDB indexes checked successfully!\")\n"
        "        except Exception as e:\n"
        "            logger.warning(f\"[DB] Index creation skipped (Offline fallback active): {e}\")"
    )
    new_init = (
        "    async def init_indexes(self):\n"
        "        if not self.has_mongo:\n"
        "            logger.warning(\"[DB] MongoDB not connected \u2014 only local /tmp storage (wiped on redeploy)\")\n"
        "            return\n"
        "        try:\n"
        "            await self.users_col.create_index(\"id\", unique=True)\n"
        "            await self.movies_col.create_index(\"movie_id\", unique=True)\n"
        "            await self.movies_col.create_index(\"normalized_title\")\n"
        "            await self.sessions_col.create_index(\"admin_id\", unique=True)\n"
        "            logger.info(\"[DB] MongoDB indexes checked successfully!\")\n"
        "        except Exception as e:\n"
        "            logger.warning(f\"[DB] Index creation skipped (Offline fallback active): {e}\")\n"
        "        # CRITICAL: pull every movie from Mongo so filters survive redeploys\n"
        "        try:\n"
        "            n = 0\n"
        "            async for doc in self.movies_col.find({}):\n"
        "                mid = doc.get(\"movie_id\")\n"
        "                if mid:\n"
        "                    self._mem_movies[mid] = doc\n"
        "                    n += 1\n"
        "            if n:\n"
        "                self._save_local_movies()\n"
        "            logger.info(f\"[DB] Loaded {n} movies from MongoDB into memory (total={len(self._mem_movies)})\")\n"
        "        except Exception as e:\n"
        "            logger.warning(f\"[DB] Failed to load movies from Mongo: {e}\")"
    )
    if old_init in text:
        text = text.replace(old_init, new_init)

    with open(_path, "w", encoding="utf-8") as f:
        f.write(text)

    spec = importlib.util.spec_from_file_location("handlers._database_restored", _path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["handlers._database_restored"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load()
db = _mod.db
Database = _mod.Database
