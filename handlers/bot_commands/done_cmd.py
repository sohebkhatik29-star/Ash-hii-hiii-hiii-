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
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("DoneCmd")

DONE_TEXT = "Your file has been uploaded successfully. Please check it."


def _is_authorized(user_id: int) -> bool:
    """Reuse existing admin/owner list (Config.ADMINS)."""
    try:
        if not user_id:
            return False
        admins = Config.ADMINS or []
        return int(user_id) in admins
    except Exception:
        return False


def register_done_command(bot: Client):
    """Register /done — group + private; admin-only; reply-required."""

    @bot.on_message(filters.command("done") & (filters.group | filters.private))
    async def done_command_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return

            if not _is_authorized(message.from_user.id):
                return

            # Must be a reply to some message — otherwise ignore completely
            target = message.reply_to_message
            if not target:
                return

            # Delete admin's /done command so it is not visible in the group
            try:
                await message.delete()
            except Exception as e:
                logger.debug("done: could not delete command msg: %s", e)

            # Reply on the original user message
            try:
                await target.reply_text(DONE_TEXT, quote=True)
            except Exception as e:
                logger.warning("done: reply failed: %s", e)
                try:
                    # Fallback: same chat, reply_to_message_id
                    await client.send_message(
                        chat_id=message.chat.id,
                        text=DONE_TEXT,
                        reply_to_message_id=target.id,
                    )
                except Exception as e2:
                    logger.warning("done: fallback reply failed: %s", e2)

        except Exception as e:
            logger.exception("done_command_handler: %s", e)

    logger.info("/done command registered")
