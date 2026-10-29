# ==============================================================================
# handlers/bot_commands/warn_cmd.py
# /warn — reply + reason; 3 warns = group suspend; admin cmd auto-delete
# ==============================================================================

import asyncio
import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus, ChatType
from pyrogram.types import Message, ChatPermissions

from configs import Config

logger = logging.getLogger("WarnCmd")

# user_id -> list of {"reason": str, "by": int, "at": str}
_WARNS: dict = {}


def _is_config_admin(user_id: int) -> bool:
    try:
        if user_id in (Config.ADMINS or set()):
            return True
    except Exception:
        pass
    try:
        if int(user_id) == int(getattr(Config, "OWNER_ID", 0) or 0):
            return True
    except Exception:
        pass
    return False


async def _is_admin(client: Client, message: Message) -> bool:
    if not message.from_user:
        return False
    uid = message.from_user.id
    if _is_config_admin(uid):
        return True
    try:
        m = await client.get_chat_member(message.chat.id, uid)
        st = m.status
        if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return True
        s = str(getattr(st, "value", st) or st).lower()
        if any(x in s for x in ("administrator", "owner", "creator")):
            return True
    except Exception:
        pass
    return False


def _extract_reason(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) > 1:
        return parts[1].strip()
    return "Rule break"


def _mute_permissions() -> ChatPermissions:
    return ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_other_messages=False,
        can_send_polls=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
    )


async def _delete_later(client: Client, chat_id: int, msg_ids: list, seconds: int = 3600):
    await asyncio.sleep(seconds)
    try:
        await client.delete_messages(chat_id, msg_ids)
    except Exception:
        pass


def register_warn_command(bot: Client):

    @bot.on_message(filters.command("warn") & filters.group, group=-12)
    async def warn_cmd(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            if not await _is_admin(client, message):
                return

            chat_id = message.chat.id
            if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
                return

            replied = message.reply_to_message

            # always delete admin /warn command
            try:
                await message.delete()
            except Exception:
                pass

            if not replied or not replied.from_user:
                tip = await client.send_message(
                    chat_id,
                    "\u26a0\ufe0f User ke message pe <b>reply</b> karke /warn likho.\n"
                    "Example:\n"
                    "<code>/warn ek din me ek request</code>\n"
                    "<code>/warn aap is group me ye nahi kar sakte</code>",
                )
                asyncio.create_task(_delete_later(client, chat_id, [tip.id], 60))
                return

            target = replied.from_user
            if target.is_bot or _is_config_admin(target.id):
                return

            # don't warn group admins
            try:
                tm = await client.get_chat_member(chat_id, target.id)
                st = tm.status
                if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                    return
                s = str(getattr(st, "value", st) or st).lower()
                if any(x in s for x in ("administrator", "owner", "creator")):
                    return
            except Exception:
                pass

            reason = _extract_reason(message)
            uid = target.id
            name = (target.first_name or "User").replace("<", "").replace(">", "")
            mention = f'<a href="tg://user?id={uid}">{name}</a>'

            entry = {
                "reason": reason,
                "by": message.from_user.id,
                "at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            }
            lst = _WARNS.setdefault(uid, [])
            lst.append(entry)
            count = len(lst)

            # try persist in db if available (best-effort)
            try:
                from handlers.database import db
                if hasattr(db, "add_warning"):
                    await db.add_warning(uid, message.from_user.id, reason)
            except Exception as e:
                logger.debug("db warn: %s", e)

            if count >= 3:
                # suspend — cannot send messages in group
                try:
                    await client.restrict_chat_member(
                        chat_id,
                        uid,
                        permissions=_mute_permissions(),
                    )
                except Exception as e:
                    logger.warning("restrict on 3 warn: %s", e)

                lines = []
                for i, w in enumerate(lst[:10], 1):
                    lines.append(f"{i}. <code>{w.get('reason', '-')}</code>")
                warns_block = "\n".join(lines)

                text = (
                    f"\U0001f6ab <b>AAP SUSPEND HO GAYE</b>\n\n"
                    f"User: {mention} (<code>{uid}</code>)\n"
                    f"Warnings: <b>{count}/3</b>\n\n"
                    f"<b>Aapko di gayi warnings:</b>\n{warns_block}\n\n"
                    f"Ab aap is group me message nahi kar sakte.\n"
                    f"Unban ke liye admin se baat karo: <code>/unbanlink {uid}</code>"
                )
            else:
                text = (
                    f"\u26a0\ufe0f <b>WARNING {count}/3</b>\n\n"
                    f"User: {mention}\n"
                    f"Reason: <code>{reason}</code>\n\n"
                    f"3 warnings ke baad aap group me message nahi kar paoge."
                )

            try:
                bot_msg = await replied.reply_text(
                    text,
                    quote=True,
                    disable_web_page_preview=True,
                )
                asyncio.create_task(
                    _delete_later(client, chat_id, [bot_msg.id], 3600)
                )
            except Exception as e:
                logger.warning("warn reply: %s", e)
                try:
                    await client.send_message(chat_id, text)
                except Exception:
                    pass

        except Exception as e:
            logger.exception("warn_cmd: %s", e)

    logger.info("/warn command registered (bot_commands)")
