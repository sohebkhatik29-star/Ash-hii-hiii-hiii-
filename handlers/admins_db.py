# handlers/admins_db.py — Manage bot admins (DB + Config.ADMINS sync + group promote)
import logging
import time
from configs import Config
from handlers.database import db

logger = logging.getLogger("AdminsDB")

KEY = "bot_extra_admins"


def _owner_id() -> int:
    try:
        return int(getattr(Config, "BOT_OWNER", 0) or 0)
    except Exception:
        return 0


def _ensure_owner_in_config():
    oid = _owner_id()
    if oid:
        try:
            Config.ADMINS.add(oid)
        except Exception:
            pass


async def _load_extra_from_db() -> list:
    """List of {user_id, name, username, added_at}."""
    if getattr(db, "_mem_extra_admins", None) is not None:
        return list(db._mem_extra_admins)
    out = []
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            doc = await col.find_one({"key": KEY})
            if doc and isinstance(doc.get("value"), list):
                out = doc["value"]
        except Exception as e:
            logger.warning("load extra admins: %s", e)
    db._mem_extra_admins = out
    return list(out)


async def _save_extra(admins: list):
    db._mem_extra_admins = list(admins)
    if getattr(db, "has_mongo", False) and getattr(db, "db", None) is not None:
        try:
            col = getattr(db, "settings_col", None) or db.db.bot_settings
            db.settings_col = col
            await col.update_one(
                {"key": KEY},
                {"$set": {"key": KEY, "value": admins}},
                upsert=True,
            )
        except Exception as e:
            logger.warning("save extra admins: %s", e)


async def sync_admins_to_config():
    """Merge DB extra admins into Config.ADMINS so rest of bot checks work."""
    _ensure_owner_in_config()
    try:
        extra = await _load_extra_from_db()
        for a in extra:
            try:
                Config.ADMINS.add(int(a.get("user_id")))
            except Exception:
                pass
    except Exception as e:
        logger.warning("sync_admins_to_config: %s", e)


async def list_all_admins() -> list:
    """
    Combined list: owner + env admins + extra DB admins.
    Each: {user_id, name, username, source: owner|env|db}
    """
    await sync_admins_to_config()
    seen = set()
    out = []
    oid = _owner_id()
    if oid:
        seen.add(oid)
        out.append({"user_id": oid, "name": "Owner", "username": getattr(Config, "OWNER_USERNAME", "") or "", "source": "owner"})

    extra = await _load_extra_from_db()
    extra_map = {int(a.get("user_id", 0)): a for a in extra if a.get("user_id")}

    # env admins from Config that are not owner
    for uid in list(getattr(Config, "ADMINS", set()) or []):
        try:
            uid = int(uid)
        except Exception:
            continue
        if uid in seen:
            continue
        seen.add(uid)
        if uid in extra_map:
            a = extra_map[uid]
            out.append({
                "user_id": uid,
                "name": a.get("name") or str(uid),
                "username": a.get("username") or "",
                "source": "db",
            })
        else:
            out.append({"user_id": uid, "name": str(uid), "username": "", "source": "env"})

    for uid, a in extra_map.items():
        if uid in seen:
            continue
        seen.add(uid)
        out.append({
            "user_id": uid,
            "name": a.get("name") or str(uid),
            "username": a.get("username") or "",
            "source": "db",
        })
        try:
            Config.ADMINS.add(uid)
        except Exception:
            pass

    return out


async def add_bot_admin(user_id: int, name: str = "", username: str = "") -> dict:
    user_id = int(user_id)
    if user_id == _owner_id():
        return {"ok": True, "msg": "Already owner"}
    extra = await _load_extra_from_db()
    for a in extra:
        if int(a.get("user_id", 0)) == user_id:
            a["name"] = name or a.get("name") or str(user_id)
            a["username"] = username or a.get("username") or ""
            await _save_extra(extra)
            Config.ADMINS.add(user_id)
            return {"ok": True, "msg": "Already admin (updated)"}
    extra.append({
        "user_id": user_id,
        "name": name or str(user_id),
        "username": username or "",
        "added_at": int(time.time()),
    })
    await _save_extra(extra)
    Config.ADMINS.add(user_id)
    return {"ok": True, "msg": "Admin added"}


async def remove_bot_admin(user_id: int) -> dict:
    user_id = int(user_id)
    if user_id == _owner_id():
        return {"ok": False, "msg": "Owner ko remove nahi kar sakte"}
    extra = await _load_extra_from_db()
    new = [a for a in extra if int(a.get("user_id", 0)) != user_id]
    if len(new) == len(extra):
        # may be env-only admin — still remove from runtime Config if not owner
        try:
            Config.ADMINS.discard(user_id)
        except Exception:
            pass
        await _save_extra(new)
        return {"ok": True, "msg": "Admin removed (runtime)"}
    await _save_extra(new)
    try:
        Config.ADMINS.discard(user_id)
    except Exception:
        pass
    return {"ok": True, "msg": "Admin removed"}


async def promote_in_group(client, user_id: int) -> str:
    """Promote user as admin in MOVIE_GROUP_ID. Returns status string."""
    gid = int(getattr(Config, "MOVIE_GROUP_ID", 0) or 0)
    if not gid:
        return "Group ID set nahi"
    user_id = int(user_id)
    try:
        from pyrogram.types import ChatPrivileges
        priv = ChatPrivileges(
            can_manage_chat=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_video_chats=False,
        )
        await client.promote_chat_member(gid, user_id, privileges=priv)
        return "Group me admin bana diya"
    except Exception as e:
        logger.warning("promote_in_group %s: %s", user_id, e)
        return f"Group promote fail: {type(e).__name__}: {e}"


async def demote_in_group(client, user_id: int) -> str:
    gid = int(getattr(Config, "MOVIE_GROUP_ID", 0) or 0)
    if not gid:
        return "Group ID set nahi"
    user_id = int(user_id)
    try:
        from pyrogram.types import ChatPrivileges
        await client.promote_chat_member(
            gid,
            user_id,
            privileges=ChatPrivileges(
                can_manage_chat=False,
                can_delete_messages=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            ),
        )
        return "Group se admin hata diya"
    except Exception as e:
        logger.warning("demote_in_group %s: %s", user_id, e)
        return f"Group demote fail: {type(e).__name__}: {e}"
