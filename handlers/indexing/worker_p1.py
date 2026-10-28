        return False


async def _copy_to_db(bot: Client, message) -> Optional[str]:
    """Copy media to DB_CHANNEL, return file_id if possible."""
    db_ch = getattr(Config, "DB_CHANNEL", None)
    if not db_ch:
        # fallback: use original file_id
        media = message.document or message.video or message.audio or message.animation
        return getattr(media, "file_id", None) if media else None
    try:
        copied = await message.copy(int(db_ch))
        media = copied.document or copied.video or copied.audio or copied.animation
        return getattr(media, "file_id", None) if media else None
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        try:
            copied = await message.copy(int(db_ch))
            media = copied.document or copied.video or copied.audio or copied.animation
            return getattr(media, "file_id", None) if media else None
        except Exception:
            media = message.document or message.video or message.audio or message.animation
            return getattr(media, "file_id", None) if media else None
    except Exception as e:
        logger.warning("copy_to_db: %s", e)
        media = message.document or message.video or message.audio or message.animation
        return getattr(media, "file_id", None) if media else None


async def _extract_poster_id(bot: Client, message) -> Optional[str]:
    try:
        from handlers.poster import extract_poster_from_message
        return await extract_poster_from_message(bot, message)
    except Exception:
        # thumb from video
        try:
            if message.video and message.video.thumbs:
                return message.video.thumbs[-1].file_id
            if message.document and message.document.thumbs:
                return message.document.thumbs[-1].file_id
        except Exception:
            pass
    return None


async def _post_movie_to_group(bot: Client, movie_id: str) -> bool:
    movie = await db.get_movie(movie_id)
    if not movie:
        return False
    try:
        card_text = build_final_movie_card_text(movie)
        buttons = build_final_movie_card_buttons(movie, for_group=True)
        target = getattr(Config, "MOVIE_GROUP_ID", None)
        if not target:
            return False
        poster_id = movie.get("poster_file_id")
        sent = None
        if poster_id:
            try:
                sent = await bot.send_photo(
                    chat_id=target,
                    photo=poster_id,
                    caption=_safe(card_text),
                    reply_markup=buttons,
                )
            except Exception:
                sent = None
        if not sent:
            sent = await bot.send_message(
                chat_id=target,
                text=_safe(card_text),
                reply_markup=buttons,
                disable_web_page_preview=True,
            )
        if sent:
            try:
                await db.update_movie_group_post(movie_id, int(target), sent.id)
            except Exception:
                pass
            return True
    except Exception as e:
        logger.error("post_movie_to_group: %s", e)
    return False


async def run_indexing(
    bot: Client,
    admin_id: int,
    channel_id: int,
    start_id: int,
    end_id: int,
    status_chat_id: int,
    status_msg_id: int = None,
):
    """Scan channel messages from start_id to end_id inclusive."""
    admin_id = int(admin_id)
    channel_id = int(channel_id)
    start_id, end_id = int(start_id), int(end_id)
    if end_id < start_id:
        start_id, end_id = end_id, start_id

    _RUNNING[admin_id] = True
    _CANCEL[admin_id] = False

    total = end_id - start_id + 1
    scanned = 0
    saved_files = 0
    movies_posted = 0
    # key (norm_title, year) -> movie_id
    bucket = {}
