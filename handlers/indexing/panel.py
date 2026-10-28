# handlers/indexing/panel.py — safe stub so bot boots
import logging
from pyrogram import Client

logger = logging.getLogger("IndexingPanel")

def register_indexing_handlers(bot: Client):
    logger.info("Indexing panel stub registered (full UI loading next deploy)")
    # no handlers — bot stays online; indexing button will show "updating"
