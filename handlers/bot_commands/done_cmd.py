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

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DoneCmd")

DONE_TEXT = "Your file has been uploaded successfully. Please check it."

# (chat_id, user_message_id) -> bot_reply_message_id
# When user msg is deleted, bot reply is deleted too.
_DONE_REPLY_MAP: dict = {}


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

            # Track: if user original msg deleted → delete this bot reply too
            if bot_msg and target:
                try:
                    key = (int(message.chat.id), int(target.id))
                    _DONE_REPLY_MAP[key] = int(bot_msg.id)
                    logger.info("done: mapped user_msg=%s → bot_reply=%s", target.id, bot_msg.id)
                except Exception as e:
                    logger.debug("done: map store failed: %s", e)

        except Exception as e:
            logger.exception("done_command_handler: %s", e)

    @bot.on_deleted_messages()
    async def done_on_user_msg_deleted(client: Client, messages):
        """If the original user message (that /done replied to) is deleted,
        also delete the bot confirmation reply."""
        try:
            if not messages:
                return
            # messages may share one chat or list of Message-like objects
            for m in messages:
                try:
                    chat_id = getattr(getattr(m, "chat", None), "id", None)
                    msg_id = getattr(m, "id", None)
                    if chat_id is None or msg_id is None:
                        continue
                    key = (int(chat_id), int(msg_id))
                    bot_reply_id = _DONE_REPLY_MAP.pop(key, None)
                    if not bot_reply_id:
                        continue
                    try:
                        await client.delete_messages(chat_id, bot_reply_id)
                        logger.info(
                            "done: user msg %s deleted → bot reply %s deleted",
                            msg_id, bot_reply_id,
                        )
                    except Exception as e:
                        logger.warning("done: could not delete bot reply %s: %s", bot_reply_id, e)
                except Exception as e:
                    logger.debug("done deleted_messages item: %s", e)
        except Exception as e:
            logger.debug("done_on_user_msg_deleted: %s", e)

    logger.info("/done command registered (group=-3 + delete-sync)")
