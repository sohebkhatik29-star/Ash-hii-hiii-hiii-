# handlers/indexing/panel.py - Indexing menu + buttons (idx_*)
import asyncio
import logging
import re
import time
from pyrogram import Client, filters, StopPropagation
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message

from configs import Config

logger = logging.getLogger("IndexingPanel")
_STATE = {}
_LOCKS = {}  # uid -> asyncio.Lock
_RECENT = {}  # key -> ts  (dedupe replies)


def _is_admin(uid: int) -> bool:
    try:
        return int(uid) in (Config.ADMINS or set() or [])
    except Exception:
        return False


def _lock_for(uid: int) -> asyncio.Lock:
    lk = _LOCKS.get(uid)
    if lk is None:
        lk = asyncio.Lock()
        _LOCKS[uid] = lk
    return lk


def _dedupe(key: str, ttl: float = 3.0) -> bool:
    """Return True if this is the first call within ttl (allow action)."""
    now = time.time()
    dead = [k for k, t in _RECENT.items() if now - t > 60]
    for k in dead:
        _RECENT.pop(k, None)
    if key in _RECENT and (now - _RECENT[key]) < ttl:
        return False
    _RECENT[key] = now
    return True


def kb_idx_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("[CH] Set Indexing Channel", callback_data="idx_set_ch")],
        [InlineKeyboardButton("[>] Start Indexing Files", callback_data="idx_list_ch")],
        [InlineKeyboardButton("[<] Back", callback_data="ap_home")],
    ])


async def open_indexing_menu(client: Client, query: CallbackQuery):
    text = (
        "[DIR] <b>Indexing Panel</b>\n\n"
        "Index movie files from a channel into the bot database "
        "and auto-post posters to the group.\n\n"
        "1) Set Indexing Channel (bot must be admin)\n"
        "2) Start Indexing -> send start & end message links"
    )
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.edit_message_text(text, reply_markup=kb_idx_menu())
    except Exception as e:
        logger.warning("open_indexing_menu edit: %s", e)


async def _safe_edit(query: CallbackQuery, text: str, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning("edit fail: %s", e)


async def _handle_idx_set_ch(client: Client, query: CallbackQuery, uid: int):
    _STATE[uid] = {"state": "wait_channel"}
    await _safe_edit(
        query,
        "[CH] <b>Set Indexing Channel</b>\n\n"
        "Channel ka ID bhejo (jaise <code>-100xxxxxxxxxx</code>)\n"
        "ya channel se koi message <b>forward</b> karo.\n\n"
        "Bot us channel me <b>admin</b> hona chahiye.",
        InlineKeyboardMarkup([[InlineKeyboardButton("[<] Back", callback_data="idx_menu")]]),
    )


async def _handle_idx_list_ch(client: Client, query: CallbackQuery, uid: int):
    channels = []
    try:
        from handlers.indexing import idx_db
        channels = await idx_db.list_channels()
    except Exception as e:
        logger.warning("list_channels: %s", e)
    if not channels:
        await _safe_edit(
            query,
            "[DIR] Koi indexing channel set nahi hai.\nPehle <b>Set Indexing Channel</b> karo.",
            kb_idx_menu(),
        )
        return
    rows = []
    for ch in channels:
        cid = int(ch.get("channel_id", 0))
        title = (ch.get("title") or str(cid))[:40]
        rows.append([InlineKeyboardButton(title, callback_data=f"idx_ch:{cid}")])
    rows.append([InlineKeyboardButton("[<] Back", callback_data="idx_menu")])
    await _safe_edit(
        query,
        "[>] <b>Select channel to index:</b>",
        InlineKeyboardMarkup(rows),
    )


async def _handle_idx_ch(client: Client, query: CallbackQuery, uid: int, cid: int):
    _STATE[uid] = {"state": "wait_start", "channel_id": cid}
    await _safe_edit(
        query,
        f"[>] Channel <code>{cid}</code>\n\n"
        "Ab <b>START</b> message ka link ya message ID bhejo\n"
        "(ya us message ko yahan forward karo).",
        InlineKeyboardMarkup([[InlineKeyboardButton("[<] Back", callback_data="idx_list_ch")]]),
    )


def register_indexing_handlers(bot: Client):

    @bot.on_callback_query(filters.regex(r"^(ap_idx_menu|idx_menu|idx_set_ch|idx_list_ch|idx_ch:)"), group=-10)
    async def _idx_router(client: Client, query: CallbackQuery):
        data = (query.data or "").strip()
        uid = int(query.from_user.id) if query.from_user else 0
        if not _dedupe(f"cb:{uid}:{data}", ttl=2.0):
            try:
                await query.answer()
            except Exception:
                pass
            raise StopPropagation
        try:
            if not _is_admin(uid):
                await query.answer("Sirf owner/admin ke liye.", show_alert=True)
                raise StopPropagation
            if data in ("ap_idx_menu", "idx_menu"):
                await open_indexing_menu(client, query)
                raise StopPropagation
            if data == "idx_set_ch":
                await _handle_idx_set_ch(client, query, uid)
                raise StopPropagation
            if data == "idx_list_ch":
                await _handle_idx_list_ch(client, query, uid)
                raise StopPropagation
            if data.startswith("idx_ch:"):
                try:
                    cid = int(data.split(":", 1)[1])
                except Exception:
                    await query.answer("Invalid channel", show_alert=True)
                    raise StopPropagation
                await _handle_idx_ch(client, query, uid, cid)
                raise StopPropagation
            raise StopPropagation
        except StopPropagation:
            raise
        except Exception as e:
            logger.exception("idx_router: %s", e)
            try:
                await query.answer(f"Error: {type(e).__name__}", show_alert=True)
            except Exception:
                pass
            raise StopPropagation

    @bot.on_message(filters.private & filters.incoming, group=-10)
    async def _idx_text(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if not _is_admin(uid):
            return
        async with _lock_for(uid):
            st = _STATE.get(uid) or {}
            state = st.get("state")
            if not state or state == "idle":
                return
            text = (message.text or "").strip()

            if state == "wait_channel":
                cid = None
                if message.forward_from_chat:
                    cid = message.forward_from_chat.id
                else:
                    try:
                        cid = int(text.replace(" ", ""))
                    except Exception:
                        if _dedupe(f"msg:{uid}:bad_ch", 2.0):
                            await message.reply_text("Valid channel ID bhejo (jaise -100...) ya forward karo.")
                        raise StopPropagation
                try:
                    from handlers.indexing import idx_db
                    title = str(cid)
                    try:
                        chat = await client.get_chat(cid)
                        title = getattr(chat, "title", None) or title
                    except Exception:
                        pass
                    await idx_db.add_channel(cid, title)
                    _STATE[uid] = {"state": "idle"}
                    if _dedupe(f"msg:{uid}:ch_set:{cid}", 3.0):
                        await message.reply_text(
                            f"[OK] Channel set: <b>{title}</b> (<code>{cid}</code>)",
                            reply_markup=kb_idx_menu(),
                        )
                except Exception as e:
                    if _dedupe(f"msg:{uid}:ch_err", 2.0):
                        await message.reply_text(f"Error: {type(e).__name__}: {e}")
                raise StopPropagation

            if state == "wait_start":
                mid = None
                if message.forward_from_chat and message.forward_from_message_id:
                    mid = message.forward_from_message_id
                    st["channel_id"] = message.forward_from_chat.id
                else:
                    m = re.search(r"/(\d+)/?$", text) or re.search(r"^(\d+)$", text)
                    if m:
                        mid = int(m.group(1))
                if not mid:
                    if _dedupe(f"msg:{uid}:need_start", 2.0):
                        await message.reply_text("Start message ID ya link bhejo.")
                    raise StopPropagation
                if (_STATE.get(uid) or {}).get("state") != "wait_start":
                    raise StopPropagation
                st["start_id"] = mid
                st["state"] = "wait_end"
                _STATE[uid] = st
                if _dedupe(f"msg:{uid}:start:{mid}", 5.0):
                    await message.reply_text(
                        f"[OK] Start = <code>{mid}</code>\nAb <b>END</b> message ID/link bhejo."
                    )
                raise StopPropagation

            if state == "wait_end":
                mid = None
                if message.forward_from_chat and message.forward_from_message_id:
                    mid = message.forward_from_message_id
                else:
                    m = re.search(r"/(\d+)/?$", text) or re.search(r"^(\d+)$", text)
                    if m:
                        mid = int(m.group(1))
                if not mid:
                    if _dedupe(f"msg:{uid}:need_end", 2.0):
                        await message.reply_text("End message ID ya link bhejo.")
                    raise StopPropagation
                if (_STATE.get(uid) or {}).get("state") != "wait_end":
                    raise StopPropagation
                st["end_id"] = mid
                st["state"] = "idle"
                _STATE[uid] = st
                channel_id = st.get("channel_id")
                start_id = st.get("start_id")
                end_id = mid
                if not _dedupe(f"job:{uid}:{channel_id}:{start_id}:{end_id}", 30.0):
                    raise StopPropagation
                status = await message.reply_text(
                    f"[IN] Indexing start...\nChannel <code>{channel_id}</code>\n"
                    f"Range <code>{start_id}</code> -> <code>{end_id}</code>"
                )
                try:
                    from handlers.session_manager import session_mgr
                    await session_mgr.cancel_session(uid)
                except Exception:
                    pass
                try:
                    from handlers.indexing.worker import run_indexing
                    asyncio.create_task(run_indexing(
                        client, uid, channel_id, start_id, end_id,
                        message.chat.id, status.id,
                    ))
                except Exception as e:
                    await status.edit_text(f"[X] Worker error: {type(e).__name__}: {e}")
                raise StopPropagation
