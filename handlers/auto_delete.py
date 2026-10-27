# ============================================================================
# handlers/auto_delete.py
# Bot file auto-delete + group result / not-found delete timings (admin settable)
# ============================================================================
import asyncio
import logging
import re
from handlers.database import db
from configs import Config

logger = logging.getLogger("AutoDelete")

# Defaults (seconds)
DEFAULT_BOT_FILE_SEC = int(getattr(Config, "AUTO_DELETE_TIME", 1800) or 1800)
DEFAULT_RESULT_SEC = 24 * 3600
DEFAULT_NOT_FOUND_SEC = 48 * 3600
DEFAULT_BOT_NOTICE = (
    "⚠️ <b>Copyright Protection Alert:</b>\n\n"
    "File will be automatically deleted in <b>{time}</b>.\n"
    "Kripya ise turant apne 'Saved Messages' mein forward kar lein!"
)

_KEYS = {
    "bot_file": "ad_bot_file_sec",
    "bot_notice": "ad_bot_notice_msg",
    "group_result": "ad_group_result_sec",
    "group_not_found": "ad_group_not_found_sec",
}


def parse_duration(text: str):
    """Parse 1m 2m 30m 1h 2h 1d 2d 24h 3month 4mahine → seconds. None if invalid."""
    if not text:
        return None
    t = text.strip().lower().replace(" ", "")
    if not t:
        return None
    if t.isdigit():
        return max(0, int(t))
    patterns = [
        (r"^(\d+)(mo|mos|month|months|mhont|mohont|mahine|mahina)$", 30 * 24 * 3600),
        (r"^(\d+)(w|wk|week|weeks)$", 7 * 24 * 3600),
        (r"^(\d+)(d|day|days|din)$", 24 * 3600),
        (r"^(\d+)(h|hr|hrs|hour|hours|ghante|ghanta)$", 3600),
        (r"^(\d+)(m|min|mins|minute|minutes)$", 60),
        (r"^(\d+)(s|sec|secs|second|seconds)$", 1),
    ]
    for pat, mult in patterns:
        m = re.match(pat, t)
        if m:
            return max(0, int(m.group(1)) * mult)
    return None


def format_duration(sec: int) -> str:
    try:
        sec = int(sec or 0)
    except Exception:
        return "0s"
    if sec <= 0:
        return "0s (off)"
    parts = []
    months = sec // (30 * 24 * 3600)
    sec %= 30 * 24 * 3600
    days = sec // (24 * 3600)
    sec %= 24 * 3600
    hours = sec // 3600
    sec %= 3600
    mins = sec // 60
    secs = sec % 60
    if months:
        parts.append(f"{months} month" + ("s" if months != 1 else ""))
    if days:
        parts.append(f"{days} day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} min" + ("s" if mins != 1 else ""))
    if secs and not parts:
        parts.append(f"{secs}s")
    return " ".join(parts) if parts else "0s"


async def _get_setting(key: str, default):
    mem_attr = f"_mem_{key}"
    val = getattr(db, mem_attr, None)
    if val is not None:
        return val
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            doc = await col.find_one({"key": key})
            if doc is not None and "value" in doc:
                setattr(db, mem_attr, doc["value"])
                return doc["value"]
        except Exception as e:
            logger.warning("get setting %s: %s", key, e)
    return default


async def _set_setting(key: str, value):
    setattr(db, f"_mem_{key}", value)
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            await col.update_one(
                {"key": key},
                {"$set": {"key": key, "value": value}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("set setting %s: %s", key, e)


async def get_bot_file_delete_seconds() -> int:
    v = await _get_setting(_KEYS["bot_file"], DEFAULT_BOT_FILE_SEC)
    try:
        return int(v)
    except Exception:
        return DEFAULT_BOT_FILE_SEC


async def set_bot_file_delete_seconds(sec: int):
    await _set_setting(_KEYS["bot_file"], int(sec))


async def get_bot_delete_notice() -> str:
    v = await _get_setting(_KEYS["bot_notice"], "")
    return (v or "").strip() or DEFAULT_BOT_NOTICE


async def set_bot_delete_notice(text: str):
    await _set_setting(_KEYS["bot_notice"], (text or "").strip())


async def clear_bot_delete_notice():
    await _set_setting(_KEYS["bot_notice"], "")


async def get_group_result_delete_seconds() -> int:
    v = await _get_setting(_KEYS["group_result"], DEFAULT_RESULT_SEC)
    try:
        return int(v)
    except Exception:
        return DEFAULT_RESULT_SEC


async def set_group_result_delete_seconds(sec: int):
    await _set_setting(_KEYS["group_result"], int(sec))


async def get_group_not_found_delete_seconds() -> int:
    v = await _get_setting(_KEYS["group_not_found"], DEFAULT_NOT_FOUND_SEC)
    try:
        return int(v)
    except Exception:
        return DEFAULT_NOT_FOUND_SEC


async def set_group_not_found_delete_seconds(sec: int):
    await _set_setting(_KEYS["group_not_found"], int(sec))


async def build_bot_notice_text() -> str:
    sec = await get_bot_file_delete_seconds()
    tpl = await get_bot_delete_notice()
    time_str = format_duration(sec)
    return (
        tpl.replace("{time}", time_str)
        .replace("{minutes}", str(max(1, sec // 60)))
        .replace("{seconds}", str(sec))
    )


async def schedule_delete(client, chat_id, message_ids, delay_seconds: int):
    if not message_ids or delay_seconds is None or delay_seconds < 0:
        return
    if delay_seconds == 0:
        return
    await asyncio.sleep(int(delay_seconds))
    for mid in message_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception as e:
            logger.debug("delete fail chat=%s mid=%s: %s", chat_id, mid, e)


def schedule_result_delete(client, chat_id, message_ids):
    """Group: result mila → configured time me user + bot result delete."""

    async def _run():
        try:
            delay = await get_group_result_delete_seconds()
        except Exception:
            delay = DEFAULT_RESULT_SEC
        await schedule_delete(client, chat_id, message_ids, delay)

    asyncio.create_task(_run())


def schedule_not_found_delete(client, chat_id, message_ids):
    """Group: result nahi mila → configured time me user + bot msg delete."""

    async def _run():
        try:
            delay = await get_group_not_found_delete_seconds()
        except Exception:
            delay = DEFAULT_NOT_FOUND_SEC
        await schedule_delete(client, chat_id, message_ids, delay)

    asyncio.create_task(_run())
