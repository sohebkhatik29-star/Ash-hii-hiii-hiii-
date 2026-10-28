# handlers/indexing/worker.py — safe stub (full indexing restored next)
import logging
from typing import Optional, Tuple

logger = logging.getLogger("IndexingWorker")
_CANCEL = {}
_RUNNING = {}

def request_cancel(admin_id: int):
    _CANCEL[int(admin_id)] = True

def is_running(admin_id: int) -> bool:
    return bool(_RUNNING.get(int(admin_id)))

def _parse_msg_link_or_forward(message):
    return None, None

async def resolve_start_end(client, message):
    return None, None, "Indexing temporarily disabled — updating. Try again in 2 min."

async def _bot_is_admin(client, channel_id: int) -> bool:
    return False

async def run_indexing(bot, admin_id, channel_id, start_id, end_id, status_chat_id, status_msg_id=None):
    logger.warning("run_indexing stub called — full worker not loaded yet")
    try:
        if status_msg_id:
            await bot.edit_message_text(
                status_chat_id, status_msg_id,
                "⚠️ Indexing engine updating. Please wait 1–2 minutes and try again."
            )
    except Exception:
        pass
