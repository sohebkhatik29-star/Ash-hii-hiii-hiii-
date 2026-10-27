# ==============================================================================
# handlers/bot_commands/delete_filter_cmd.py
# Command: /deletefilter  (aliases: /delete_filter, /delfilter, /df)
# Admin replies to a group poster/available post →
#   1) deletes the admin's command message
#   2) removes that movie from the filter list (DB) by group_msg_id
# Without reply-to: does nothing.
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config
from handlers.database import db

logger = logging.getLogger("DeleteFilterCmd")


async def _is_authorized(client: Client, message: Message) -> bool:
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
        logger.debug("deletefilter admin check: %s", e)

    return False


def register_delete_filter_command(bot: Client):
    """Register /deletefilter — reply to poster to remove filter from list."""

    @bot.on_message(
        filters.command(["deletefilter", "delete_filter", "delfilter", "df"])
        & (filters.group | filters.private),
        group=-3,
    )
    async def delete_filter_handler(client: Client, message: Message):
        try:
            logger.info(
                "deletefilter cmd chat=%s user=%s reply=%s",
                getattr(message.chat, "id", None),
                getattr(message.from_user, "id", None),
                bool(message.reply_to_message),
            )

            if not message.from_user:
                return

            if not await _is_authorized(client, message):
                logger.info("deletefilter: not authorized user=%s", message.from_user.id)
                return

            target = message.reply_to_message
            if not target:
                logger.info("deletefilter: no reply_to_message — ignore")
                return

            # Delete admin command first
            try:
                await message.delete()
            except Exception as e:
                logger.warning("deletefilter: delete cmd failed: %s", e)

            msg_id = int(target.id)
            deleted_ids = []
            try:
                deleted_ids = await db.delete_by_group_msg_id(msg_id)
            except Exception as e:
                logger.error("deletefilter: DB delete failed: %s", e)
                return

            if deleted_ids:
                logger.info(
                    "deletefilter: removed movie_ids=%s for group_msg_id=%s",
                    deleted_ids, msg_id,
                )
            else:
                logger.info(
                    "deletefilter: no filter found for group_msg_id=%s",
                    msg_id,
                )

        except Exception as e:
            logger.exception("delete_filter_handler: %s", e)

    logger.info("/deletefilter registered")
