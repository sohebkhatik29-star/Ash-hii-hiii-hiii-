# =============================================================================
# handlers/indexing/idx_db.py — store indexing channels
# =============================================================================
import logging
from typing import List, Optional

from handlers.database import db

logger = logging.getLogger("IndexingDB")
_MEM_CHANNELS: dict = {}  # channel_id -> {channel_id, title, username}


def _col():
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        return db.db["indexing_channels"]
    return None


async def add_channel(channel_id: int, title: str = "", username: str = "") -> dict:
    channel_id = int(channel_id)
    doc = {
        "channel_id": channel_id,
        "title": title or str(channel_id),
        "username": (username or "").lstrip("@"),
    }
    col = _col()
    if col is not None:
        await col.update_one({"channel_id": channel_id}, {"$set": doc}, upsert=True)
    _MEM_CHANNELS[channel_id] = doc
    return doc


async def remove_channel(channel_id: int) -> bool:
    channel_id = int(channel_id)
    col = _col()
    ok = False
    if col is not None:
        res = await col.delete_one({"channel_id": channel_id})
        ok = bool(res.deleted_count)
    if channel_id in _MEM_CHANNELS:
        _MEM_CHANNELS.pop(channel_id, None)
        ok = True
    return ok


async def list_channels() -> List[dict]:
    col = _col()
    if col is not None:
        out = []
        async for doc in col.find({}):
            out.append(doc)
            _MEM_CHANNELS[int(doc["channel_id"])] = doc
        return out
    return list(_MEM_CHANNELS.values())


async def get_channel(channel_id: int) -> Optional[dict]:
    channel_id = int(channel_id)
    if channel_id in _MEM_CHANNELS:
        return _MEM_CHANNELS[channel_id]
    col = _col()
    if col is not None:
        doc = await col.find_one({"channel_id": channel_id})
        if doc:
            _MEM_CHANNELS[channel_id] = doc
            return doc
    return None
