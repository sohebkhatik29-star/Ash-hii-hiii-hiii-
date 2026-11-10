# ==============================================================================
# handlers/bot_commands/notavailable_cmd.py
# /not_available (aliases: /notavailable, /navailable)
# - Group: reply to user msg → bot replies not-available text, deletes cmd
# - Private on Movie Request card / forward → same text in GROUP + delete nahi-mila
# ==============================================================================

import asyncio
import logging
from pyrogram import Client, filters, StopPropagation
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import Message

from configs import Config

logger = logging.getLogger("NotAvailableCmd")

NOT_AVAILABLE_TEXT = (
    "This series or movie is not available on Telegram.\n\n"
    "We could not find it in our library at the moment. "
    "Please double-check the spelling or try again later. "
    "If it gets released or uploaded, it will be added here."
)

_NA_REPLY_MAP: dict = {}
_WATCH_INTERVAL = 3
_WATCH_MAX_CHECKS = 200


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
    if getattr(Config, "BOT_OWNER", None) and user_id == Config.BOT_OWNER:
        return True
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
        logger.debug("notavailable admin check: %s", e)
    return False


async def _delete_bot_replies(client: Client, chat_id: int, bot_ids: list):
    if not bot_ids:
        return
    try:
        await client.delete_messages(chat_id, bot_ids)
    except Exception:
        for mid in bot_ids:
            try:
                await client.delete_messages(chat_id, mid)
            except Exception:
                pass


async def _watch_user_msg(client: Client, chat_id: int, user_mid: int, bot_mid: int):
    for _ in range(_WATCH_MAX_CHECKS):
        await asyncio.sleep(_WATCH_INTERVAL)
        try:
            msg = await client.get_messages(chat_id, user_mid)
            if msg is None or getattr(msg, "empty", False):
                from handlers.bot_commands.dn_cmd import cascade_delete_for_user_msg
                await cascade_delete_for_user_msg(client, chat_id, user_mid)
                return
        except Exception:
            try:
                from handlers.bot_commands.dn_cmd import cascade_delete_for_user_msg
                await cascade_delete_for_user_msg(client, chat_id, user_mid)
            except Exception:
                pass
            return


async def _complete_not_available_in_group(client: Client, meta: dict) -> bool:
    """Send NOT_AVAILABLE in group on user msg + delete nahi-mila."""
    from handlers.bot_commands.dn_cmd import complete_request_in_group

    return await complete_request_in_group(
        client,
        meta,
        text=NOT_AVAILABLE_TEXT,
        prefer_done_text=False,
    )


def register_notavailable_command(bot: Client):
    @bot.on_message(
        filters.command(["not_available", "notavailable", "navailable"])
        & (filters.group | filters.private),
        group=-4,
    )
    async def notavailable_handler(client: Client, message: Message):
        try:
            if not message.from_user:
                return
            if not await _is_authorized(client, message):
                return

            target = message.reply_to_message
            if not target:
                return

            is_private = (
                message.chat and message.chat.type == ChatType.PRIVATE
            )

            # ---- Private: Movie Request card / forward → group ----
            if is_private:
                try:
                    from handlers.bot_commands.dn_cmd import (
                        resolve_request_meta_async,
                    )
                    meta = await resolve_request_meta_async(
                        client,
                        target,
                        admin_id=message.from_user.id,
                        private_chat_id=message.chat.id,
                    )
                    if meta:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        ok = await _complete_not_available_in_group(client, meta)
                        try:
                            await client.send_message(
                                message.chat.id,
                                "✅ Group me user ko 'not available' reply + nahi-mila delete."
                                if ok
                                else "⚠️ Group reply fail — bot group me admin + send permission check karo.",
                            )
                        except Exception:
                            pass
                        logger.info(
                            "not_available(private→group) ok=%s chat=%s msg=%s",
                            ok,
                            meta.get("chat_id"),
                            meta.get("user_msg_id"),
                        )
                        raise StopPropagation
                    body = target.text or target.caption or ""
                    looks_req = (
                        "Movie Request" in body
                        or "chat=" in body
                        or "nfmsg=" in body
                        or bool(getattr(target, "forward_date", None))
                        or bool(getattr(target, "forward_origin", None))
                        or bool(getattr(target, "forward_from_chat", None))
                    )
                    if looks_req:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        await client.send_message(
                            message.chat.id,
                            "Is message se group request nahi mila.\n\n"
                            "<b>📥 Movie Request (not found)</b> card pe "
                            "(jisme <code>chat=… msg=…</code> ho) reply karke "
                            "<code>/not_available</code> bhejo.",
                        )
                        raise StopPropagation
                except StopPropagation:
                    raise
                except Exception as e:
                    logger.warning("not_available private path: %s", e)
                # fall through only if not a request card (shouldn't reply local on random PM)
                return

            # ---- Group: classic reply on user message ----
            try:
                await message.delete()
            except Exception as e:
                logger.warning("notavailable: delete cmd failed: %s", e)

            # also delete nahi-mila if known
            try:
                from handlers.bot_commands.dn_cmd import _NOT_FOUND_MSG
                nf = _NOT_FOUND_MSG.pop(
                    (int(message.chat.id), int(target.id)), None
                )
                if nf:
                    try:
                        await client.delete_messages(message.chat.id, int(nf))
                    except Exception:
                        pass
            except Exception:
                pass

            bot_msg = None
            try:
                bot_msg = await client.send_message(
                    chat_id=message.chat.id,
                    text=NOT_AVAILABLE_TEXT,
                    reply_to_message_id=target.id,
                )
            except Exception as e:
                logger.warning("notavailable: send reply failed: %s", e)
                try:
                    bot_msg = await target.reply_text(NOT_AVAILABLE_TEXT, quote=True)
                except Exception as e2:
                    logger.warning("notavailable: fallback reply failed: %s", e2)

            if bot_msg and target:
                try:
                    chat_id = int(message.chat.id)
                    user_mid = int(target.id)
                    bot_mid = int(bot_msg.id)
                    from handlers.bot_commands.dn_cmd import track_bot_reply
                    track_bot_reply(chat_id, user_mid, bot_mid)
                    # backup poll in case deleted_messages misses
                    asyncio.create_task(
                        _watch_user_msg(client, chat_id, user_mid, bot_mid)
                    )
                except Exception as e:
                    logger.debug("notavailable map/watch: %s", e)

        except StopPropagation:
            raise
        except Exception as e:
            logger.exception("notavailable_handler: %s", e)

    # deleted_messages cascade is handled centrally in dn_cmd.dn_on_deleted
    logger.info("/not_available registered (private→group + group + cleanup)")
