# ==============================================================================
# handlers/bot_commands/done_cmd.py
# Command: /done
# Admin replies to a user message with /done →
#   1) deletes the admin's /done message
#   2) replies on the original user message:
#      "Your file has been uploaded successfully. Please check it."
# Without reply-to: does nothing.
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DoneCmd")

DONE_TEXT = "Your file has been uploaded successfully. Please check it."


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
    """Register /done — group + private; admin-only; reply-required."""

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

            try:
                await client.send_message(
                    chat_id=message.chat.id,
                    text=DONE_TEXT,
                    reply_to_message_id=target.id,
                )
            except Exception as e:
                logger.warning("done: send reply failed: %s", e)
                try:
                    await target.reply_text(DONE_TEXT, quote=True)
                except Exception as e2:
                    logger.warning("done: fallback reply failed: %s", e2)

        except Exception as e:
            logger.exception("done_command_handler: %s", e)

    logger.info("/done command registered (group=-3)")
