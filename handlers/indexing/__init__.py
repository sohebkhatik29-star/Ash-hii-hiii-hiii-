# handlers/indexing — Channel file indexing system (fail-safe)
def register_indexing_handlers(bot):
    try:
        from handlers.indexing.panel import register_indexing_handlers as _reg
        _reg(bot)
    except Exception as e:
        import logging
        logging.getLogger("Indexing").error("indexing panel load failed: %s", e)

__all__ = ["register_indexing_handlers"]
