# ============================================================================
# handlers/auto_delete.py  — ONLY auto-delete timings for filter messages
# Result mila     → 24 hours (bot reply + user message)
# Result nahi mila → 48 hours (bot reply + user message)
# ============================================================================
import asyncio
import logging

logger = logging.getLogger("AutoDelete")

RESULT_DELETE_SECONDS = 24 * 3600      # 24 hours — result milne par
NOT_FOUND_DELETE_SECONDS = 48 * 3600   # 48 hours — result na milne par


async def schedule_delete(client, chat_id, message_ids, delay_seconds: int):
    """delay_seconds ke baad message_ids delete."""
    if not message_ids or delay_seconds is None or delay_seconds < 0:
        return
    await asyncio.sleep(int(delay_seconds))
    for mid in message_ids:
        try:
            await client.delete_messages(chat_id, mid)
        except Exception as e:
            logger.debug("delete fail chat=%s mid=%s: %s", chat_id, mid, e)


def schedule_result_delete(client, chat_id, message_ids):
    """Result mila → 24h me delete (bot + user message)."""
    asyncio.create_task(
        schedule_delete(client, chat_id, message_ids, RESULT_DELETE_SECONDS)
    )


def schedule_not_found_delete(client, chat_id, message_ids):
    """Result nahi mila → 48h me delete (bot + user message)."""
    asyncio.create_task(
        schedule_delete(client, chat_id, message_ids, NOT_FOUND_DELETE_SECONDS)
    )
