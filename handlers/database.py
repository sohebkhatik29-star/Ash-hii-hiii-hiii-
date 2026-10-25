# handlers/database.py — compact working version (movies_col compatible)
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from configs import Config

STOPWORDS = {
    "the","a","an","and","or","of","in","on","to","for","is","it","me","my","you",
    "full","movie","film","hd","bluray","webrip","web","dl","x264","x265","hevc",
    "hindi","english","dual","audio","org","official","print","cam","ts","tc",
}

class Database:
    def __init__(self, url: str, name: str):
        self.client = AsyncIOMotorClient(url)
        self.db = self.client[name]
        self.movies_col = self.db.movies
        self.movies = self.movies_col
        self.force_sub = self.db.force_sub
        self.warns = self.db.warns
        self.users = self.db.users
        self.settings = self.db.settings
        self.has_mongo = True

    async def search_movies(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        tokens = [t for t in re.split(r"[^a-z0-9]+", q) if t and t not in STOPWORDS and len(t) > 1]
        if not tokens:
            tokens = [q]
        regexes = [{"title": {"$regex": re.escape(t), "$options": "i"}} for t in tokens]
        cursor = self.movies_col.find({"$or": regexes}).sort("created_at", -1).limit(limit * 3)
        rows = await cursor.to_list(length=limit * 3)
        scored = []
        for r in rows:
            title = (r.get("title") or "").lower()
            hit = sum(1 for t in tokens if t in title)
            if hit * 2 >= len(tokens):
                scored.append((hit, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    async def add_movie(self, doc: Dict[str, Any]) -> str:
        res = await self.movies_col.insert_one(doc)
        return str(res.inserted_id)

    async def get_movie(self, movie_id: str) -> Optional[Dict[str, Any]]:
        from bson import ObjectId
        try:
            return await self.movies_col.find_one({"_id": ObjectId(movie_id)})
        except Exception:
            return await self.movies_col.find_one({"movie_id": movie_id})

    async def update_movie(self, movie_id: str, data: Dict[str, Any]) -> bool:
        from bson import ObjectId
        try:
            res = await self.movies_col.update_one({"_id": ObjectId(movie_id)}, {"$set": data})
        except Exception:
            res = await self.movies_col.update_one({"movie_id": movie_id}, {"$set": data})
        return res.modified_count > 0

    async def delete_movie(self, movie_id: str) -> bool:
        from bson import ObjectId
        try:
            res = await self.movies_col.delete_one({"_id": ObjectId(movie_id)})
        except Exception:
            res = await self.movies_col.delete_one({"movie_id": movie_id})
        return res.deleted_count > 0

    async def count_movies(self) -> int:
        return await self.movies_col.count_documents({})

    async def get_force_channels(self) -> List[Dict[str, Any]]:
        return await self.force_sub.find({}).to_list(length=20)

    async def set_force_channels(self, channels: List[Dict[str, Any]]) -> None:
        await self.force_sub.delete_many({})
        if channels:
            await self.force_sub.insert_many(channels)

    async def add_force_channel(self, ch: Dict[str, Any]) -> None:
        await self.force_sub.update_one(
            {"chat_id": ch.get("chat_id")},
            {"$set": ch},
            upsert=True,
        )

    async def remove_force_channel(self, chat_id: int) -> bool:
        res = await self.force_sub.delete_one({"chat_id": chat_id})
        return res.deleted_count > 0

    async def get_warns(self, user_id: int) -> int:
        doc = await self.warns.find_one({"user_id": user_id})
        return int(doc.get("count", 0)) if doc else 0

    async def add_warn(self, user_id: int) -> int:
        doc = await self.warns.find_one_and_update(
            {"user_id": user_id},
            {"$inc": {"count": 1}},
            upsert=True,
            return_document=True,
        )
        return int(doc.get("count", 1))

    async def reset_warns(self, user_id: int) -> None:
        await self.warns.delete_one({"user_id": user_id})

db = Database(Config.DATABASE_URL, Config.DATABASE_NAME)
