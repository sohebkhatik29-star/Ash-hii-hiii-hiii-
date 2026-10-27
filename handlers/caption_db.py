# handlers/caption_db.py — custom file caption template (admin set)
import logging
from handlers.database import db

logger = logging.getLogger("CaptionDB")

async def get_file_caption():
    if getattr(db, "_mem_file_caption", None):
        return db._mem_file_caption
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            doc = await col.find_one({"key": "file_caption"})
            if doc and doc.get("value"):
                db._mem_file_caption = doc["value"]
                return db._mem_file_caption
        except Exception as e:
            logger.warning("get_file_caption: %s", e)
    return ""

async def set_file_caption(text: str):
    text = (text or "").strip()
    db._mem_file_caption = text
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            await col.update_one(
                {"key": "file_caption"},
                {"$set": {"key": "file_caption", "value": text}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("set_file_caption: %s", e)

async def clear_file_caption():
    db._mem_file_caption = None
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            await col.delete_one({"key": "file_caption"})
        except Exception as e:
            logger.warning("clear_file_caption: %s", e)

def format_caption_template(template: str, file_name: str = "", file_size: str = "", file_caption: str = "") -> str:
    """Replace placeholders in custom caption template."""
    if not template:
        return file_caption or ""
    out = template
    out = out.replace("{file_name}", file_name or "")
    out = out.replace("{filename}", file_name or "")
    out = out.replace("{file_size}", file_size or "")
    out = out.replace("{filesize}", file_size or "")
    out = out.replace("{file_caption}", file_caption or "")
    out = out.replace("{caption}", file_caption or "")
    return out

async def build_delivery_caption(file_name: str = "", file_size: str = "", original_caption: str = "") -> str | None:
    """
    Returns custom caption if set, else None (caller keeps original).
    """
    try:
        tpl = await get_file_caption()
    except Exception:
        tpl = ""
    if not tpl:
        return None
    return format_caption_template(tpl, file_name=file_name, file_size=file_size, file_caption=original_caption or "")

def _human_size(n) -> str:
    try:
        n = int(n or 0)
    except Exception:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} PB"

def media_meta_from_message(msg) -> tuple:
    """Extract (file_name, file_size_str, original_caption) from a Message."""
    if not msg:
        return "", "", ""
    name = ""
    size = 0
    media = msg.video or msg.document or msg.animation or msg.audio
    if media:
        name = getattr(media, "file_name", None) or ""
        size = getattr(media, "file_size", 0) or 0
    if not name and msg.video:
        name = "video.mp4"
    cap = msg.caption or ""
    return name or "", _human_size(size), cap

def patch_db():
    if not hasattr(db, "_mem_file_caption"):
        db._mem_file_caption = None
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        if getattr(db, "settings_col", None) is None:
            try:
                db.settings_col = db.db.bot_settings
            except Exception:
                pass
    for name, fn in [
        ("get_file_caption", get_file_caption),
        ("set_file_caption", set_file_caption),
        ("clear_file_caption", clear_file_caption),
        ("build_delivery_caption", build_delivery_caption),
    ]:
        setattr(db, name, fn)

patch_db()
