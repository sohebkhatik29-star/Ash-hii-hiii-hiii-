# Auto-patch force-sub methods onto db
import logging
import time
from handlers.database import db

logger = logging.getLogger("ForceSubDB")
MAX = 7

async def list_force_channels():
    if getattr(db, "has_mongo", False) and getattr(db, "force_sub_col", None) is not None:
        try:
            docs = []
            async for d in db.force_sub_col.find({}).sort("added_at", 1):
                d.pop("_id", None)
                docs.append(d)
            db._mem_force_channels = docs
            return docs
        except Exception as e:
            logger.warning(f"list_force_channels: {e}")
    return list(getattr(db, "_mem_force_channels", []) or [])

async def count_force_channels():
    return len(await list_force_channels())

async def add_force_channel(channel_id, title="", username="", mode="normal"):
    existing = await list_force_channels()
    for c in existing:
        if int(c.get("channel_id", 0)) == int(channel_id):
            c["mode"] = mode
            c["title"] = title or c.get("title") or ""
            c["username"] = username or c.get("username") or ""
            if getattr(db, "has_mongo", False) and getattr(db, "force_sub_col", None) is not None:
                await db.force_sub_col.update_one({"channel_id": int(channel_id)}, {"$set": c}, upsert=True)
            return c
    if len(existing) >= MAX:
        raise ValueError("MAX_7")
    doc = {
        "channel_id": int(channel_id),
        "title": title or str(channel_id),
        "username": username or "",
        "mode": mode if mode in ("normal", "request") else "normal",
        "added_at": int(time.time()),
    }
    if not hasattr(db, "_mem_force_channels"):
        db._mem_force_channels = []
    db._mem_force_channels.append(doc)
    if getattr(db, "has_mongo", False):
        if getattr(db, "db", None) is not None and getattr(db, "force_sub_col", None) is None:
            try:
                db.force_sub_col = db.db.force_sub_channels
            except Exception:
                pass
        if getattr(db, "force_sub_col", None) is not None:
            await db.force_sub_col.update_one({"channel_id": int(channel_id)}, {"$set": doc}, upsert=True)
    return doc

async def remove_force_channel(channel_id):
    cid = int(channel_id)
    mem = getattr(db, "_mem_force_channels", []) or []
    db._mem_force_channels = [c for c in mem if int(c.get("channel_id", 0)) != cid]
    removed = False
    if getattr(db, "has_mongo", False):
        if getattr(db, "force_sub_col", None) is None and getattr(db, "db", None) is not None:
            try:
                db.force_sub_col = db.db.force_sub_channels
            except Exception:
                pass
        if getattr(db, "force_sub_col", None) is not None:
            res = await db.force_sub_col.delete_one({"channel_id": cid})
            removed = res.deleted_count > 0
    return removed or True

async def get_force_sub_message():
    if getattr(db, "_mem_force_msg", None):
        return db._mem_force_msg
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            doc = await col.find_one({"key": "force_sub_message"})
            if doc and doc.get("value"):
                db._mem_force_msg = doc["value"]
                return db._mem_force_msg
        except Exception:
            pass
    return ""

async def set_force_sub_message(text):
    db._mem_force_msg = text
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            await col.update_one(
                {"key": "force_sub_message"},
                {"$set": {"key": "force_sub_message", "value": text}},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"set_force_sub_message: {e}")

async def clear_force_sub_message():
    db._mem_force_msg = None
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            await col.delete_one({"key": "force_sub_message"})
        except Exception:
            pass

def patch_db():
    if not hasattr(db, "_mem_force_channels"):
        db._mem_force_channels = []
    if not hasattr(db, "_mem_force_msg"):
        db._mem_force_msg = None
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        if getattr(db, "force_sub_col", None) is None:
            try:
                db.force_sub_col = db.db.force_sub_channels
            except Exception:
                pass
        if getattr(db, "settings_col", None) is None:
            try:
                db.settings_col = db.db.bot_settings
            except Exception:
                pass
    for name, fn in [
        ("list_force_channels", list_force_channels),
        ("count_force_channels", count_force_channels),
        ("add_force_channel", add_force_channel),
        ("remove_force_channel", remove_force_channel),
        ("get_force_sub_message", get_force_sub_message),
        ("set_force_sub_message", set_force_sub_message),
        ("clear_force_sub_message", clear_force_sub_message),
    ]:
        setattr(db, name, fn)

patch_db()
