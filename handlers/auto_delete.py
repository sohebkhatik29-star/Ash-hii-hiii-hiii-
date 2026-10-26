# ============================================================================
# handlers/auto_delete.py  — ONLY auto-delete timings for filter messages
# Testing abhi: dono 2 minute
# Baad me change:
#   RESULT_DELETE_SECONDS   = 24 * 3600   # 24 hours
#   NOT_FOUND_DELETE_SECONDS = 48 * 3600  # 48 hours
# ============================================================================
import asyncio
import logging

logger = logging.getLogger("AutoDelete")

# ---- TESTING (abhi) ----
RESULT_DELETE_SECONDS = 2 * 60       # 2 minute — result milne par
NOT_FOUND_DELETE_SECONDS = 2 * 60    # 2 minute — result na milne par

# ---- PRODUCTION (baad me ye values lagana) ----
# RESULT_DELETE_SECONDS = 24 * 3600      # 24 hours
# NOT_FOUND_DELETE_SECONDS = 48 * 3600   # 48 hours


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
    """Result mila — testing 2 min, baad me 24h."""
    asyncio.create_task(
        schedule_delete(client, chat_id, message_ids, RESULT_DELETE_SECONDS)
    )


def schedule_not_found_delete(client, chat_id, message_ids):
    """Result nahi mila — testing 2 min, baad me 48h."""
    asyncio.create_task(
        schedule_delete(client, chat_id, message_ids, NOT_FOUND_DELETE_SECONDS)
    )
