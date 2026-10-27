# ==============================================================================
# handlers/bot_commands/done_cmd.py
# Command: /done
# Admin replies to a user message with /done →
#   1) deletes the admin's /done message
#   2) replies on the original user message:
#      "Your file has been uploaded successfully. Please check it."
#   3) If that original user message is later deleted from the group,
#      the bot's confirmation reply is also deleted automatically.
# Without reply-to: does nothing.
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DoneCmd")

DONE_TEXT = "Your file has been uploaded successfully. Please check it."

# (chat_id, user_message_id) -> [bot_reply_message_ids]
_DONE_REPLY_MAP: dict = {}

# How long / how often to poll if user msg still exists (deleted_messages is unreliable in groups)
_WATCH_INTERVAL = 3       # seconds between checks
_WATCH_MAX_CHECKS = 200   # ~10 minutes coverage


async def _is_authorized(client: Client, message: Message) -> bool:
    """Config.ADMINS / owner OR Telegram group admin/owner (same as moderation)."""
    if not message.from_user:
        return False
    user_id = message.from_user.id

    try:
        from handlers.admins_db import sync_admins_to_config
        await sync_admins_to_config()
    except Exception:
        pass

    try:
        if user_id in (Config.ADMINS or set()):
            return True
    except Exception:
        pass

    try:
        if message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            member = await client.get_chat_member(message.chat.id, user_id)
            st = member.status
            if st in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                return True
            st_s = str(getattr(st, "value", st) or st).lower()
            if any(x in st_s for x in ("administrator", "owner", "creator")):
                return True
    except Exception as e:
        logger.debug("done admin check: %s", e)

    return False


async def _delete_bot_replies(client: Client, chat_id: int, bot_ids: list):
    if not bot_ids:
        return
    try:
        await client.delete_messages(chat_id, bot_ids)
        logger.info("done: deleted bot replies %s in chat %s", bot_ids, chat_id)
    except Exception as e:
        # try one-by-one
        for mid in bot_ids:
            try:
                await client.delete_messages(chat_id, mid)
            except Exception as e2:
                logger.warning("done: delete bot reply %s failed: %s", mid, e2)


def _user_msg_gone(msg) -> bool:
    if msg is None:
        return True
    if isinstance(msg, list):
        if not msg:
            return True
        msg = msg[0]
    if getattr(msg, "empty", False):
        return True
    # Message with no content markers sometimes means deleted/empty
    if not getattr(msg, "id", None):
        return True
    return False


async def _watch_user_msg(
    client: Client,
    chat_id: int,
    user_msg_id: int,
    bot_msg_id: int,
):
    """Poll until user message is gone, then delete linked bot confirmation(s)."""
    key = (int(chat_id), int(user_msg_id))
    try:
        for _ in range(_WATCH_MAX_CHECKS):
            await asyncio.sleep(_WATCH_INTERVAL)
            try:
                msg = await client.get_messages(chat_id, user_msg_id)
            except Exception:
                msg = None

            if _user_msg_gone(msg):
                bot_ids = _DONE_REPLY_MAP.pop(key, None)
                if not bot_ids:
                    bot_ids = [int(bot_msg_id)]
                await _delete_bot_replies(client, chat_id, list(bot_ids))
                return

            # still exists — keep waiting
        # timeout: stop watching, leave map entry (deleted_messages may still fire)
        logger.debug("done: watch timeout chat=%s user_msg=%s", chat_id, user_msg_id)
    except Exception as e:
        logger.debug("done watch: %s", e)


def register_done_command(bot: Client):
    """Register /done + auto-cleanup when original user message is deleted."""

    @bot.on_message(
        filters.command(["done"]) & (filters.group | filters.private),
        group=-3,
    )
    async def done_command_handler(client: Client, message: Message):
        try:
            logger.info(
                "done cmd chat=%s user=%s reply=%s text=%r",
                getattr(message.chat, "id", None),
                getattr(message.from_user, "id", None),
                bool(message.reply_to_message),
                (message.text or "")[:80],
            )

            if not message.from_user:
                return

            if not await _is_authorized(client, message):
                logger.info("done: not authorized user=%s", message.from_user.id)
                return

            target = message.reply_to_message
            if not target:
                logger.info("done: no reply_to_message — ignore")
                return

            try:
                await message.delete()
            except Exception as e:
                logger.warning("done: delete cmd failed: %s", e)

            bot_msg = None
            try:
                bot_msg = await client.send_message(
                    chat_id=message.chat.id,
                    text=DONE_TEXT,
                    reply_to_message_id=target.id,
                )
            except Exception as e:
                logger.warning("done: send reply failed: %s", e)
                try:
                    bot_msg = await target.reply_text(DONE_TEXT, quote=True)
                except Exception as e2:
                    logger.warning("done: fallback reply failed: %s", e2)

            if bot_msg and target:
                try:
                    chat_id = int(message.chat.id)
                    user_mid = int(target.id)
                    bot_mid = int(bot_msg.id)
                    key = (chat_id, user_mid)
                    _DONE_REPLY_MAP.setdefault(key, [])
                    if bot_mid not in _DONE_REPLY_MAP[key]:
                        _DONE_REPLY_MAP[key].append(bot_mid)
                    logger.info(
                        "done: mapped user_msg=%s → bot_reply=%s",
                        user_mid, bot_mid,
                    )
                    # Reliable cleanup: poll until user msg is gone
                    asyncio.create_task(
                        _watch_user_msg(client, chat_id, user_mid, bot_mid)
                    )
                except Exception as e:
                    logger.debug("done: map/watch setup failed: %s", e)

        except Exception as e:
            logger.exception("done_command_handler: %s", e)

    @bot.on_deleted_messages()
    async def done_on_user_msg_deleted(client: Client, messages):
        """Backup: if Telegram sends delete update, remove bot confirmations."""
        try:
            if not messages:
                return
            for m in messages:
                try:
                    chat = getattr(m, "chat", None)
                    chat_id = getattr(chat, "id", None) if chat else None
                    msg_id = getattr(m, "id", None)
                    if chat_id is None or msg_id is None:
                        continue
                    key = (int(chat_id), int(msg_id))
                    bot_ids = _DONE_REPLY_MAP.pop(key, None)
                    if not bot_ids:
                        continue
                    await _delete_bot_replies(client, int(chat_id), list(bot_ids))
                except Exception as e:
                    logger.debug("done deleted_messages item: %s", e)
        except Exception as e:
            logger.debug("done_on_user_msg_deleted: %s", e)

    logger.info("/done registered (watch + deleted_messages cleanup)")
