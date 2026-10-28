# handlers/indexing/panel.py — minimal Indexing menu (ap_idx_menu opens)
import logging
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message

from configs import Config

logger = logging.getLogger("IndexingPanel")
_STATE = {}

def _is_admin(uid: int) -> bool:
    try:
        return int(uid) in (Config.ADMINS or set() or [])
    except Exception:
        return False

def kb_idx_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Set Indexing Channel", callback_data="idx_set_ch")],
        [InlineKeyboardButton("▶️ Start Indexing Files", callback_data="idx_list_ch")],
        [InlineKeyboardButton("⬅️ Back", callback_data="ap_home")],
    ])

async def open_indexing_menu(client: Client, query: CallbackQuery):
    text = (
        "📂 <b>Indexing Panel</b>\n\n"
        "Index movie files from a channel into the bot database "
        "and auto-post posters to the group.\n\n"
        "1) Set Indexing Channel (bot must be admin)\n"
        "2) Start Indexing → send start & end message links"
    )
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.edit_message_text(text, reply_markup=kb_idx_menu())
    except Exception as e:
        logger.warning("open_indexing_menu edit: %s", e)
        try:
            await client.send_message(query.message.chat.id, text, reply_markup=kb_idx_menu())
        except Exception as e2:
            logger.warning("open_indexing_menu send: %s", e2)

def register_indexing_handlers(bot: Client):
    @bot.on_callback_query(filters.regex(r"^ap_idx_menu$"), group=-5)
    async def _ap_idx_open(client: Client, query: CallbackQuery):
        try:
            if not _is_admin(query.from_user.id if query.from_user else 0):
                await query.answer("Sirf owner/admin ke liye.", show_alert=True)
                return
            await open_indexing_menu(client, query)
        except Exception as e:
            logger.exception("ap_idx_menu: %s", e)
            try:
                await query.answer(f"Error: {type(e).__name__}", show_alert=True)
            except Exception:
                pass

    @bot.on_callback_query(filters.regex(r"^idx_menu$"), group=-5)
    async def _idx_menu(client: Client, query: CallbackQuery):
        if not _is_admin(query.from_user.id if query.from_user else 0):
            await query.answer("Sirf admin.", show_alert=True)
            return
        await open_indexing_menu(client, query)

    @bot.on_callback_query(filters.regex(r"^idx_set_ch$"), group=-5)
    async def _idx_set_ch(client: Client, query: CallbackQuery):
        uid = query.from_user.id if query.from_user else 0
        if not _is_admin(uid):
            await query.answer("Sirf admin.", show_alert=True)
            return
        _STATE[uid] = {"state": "wait_channel"}
        await query.answer()
        await query.edit_message_text(
            "📡 <b>Set Indexing Channel</b>\n\n"
            "Channel ka ID bhejo (jaise <code>-100xxxxxxxxxx</code>)\n"
            "ya channel se koi message forward karo.\n\n"
            "Bot us channel me <b>admin</b> hona chahiye.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="idx_menu")]]),
        )

    @bot.on_callback_query(filters.regex(r"^idx_list_ch$"), group=-5)
    async def _idx_list_ch(client: Client, query: CallbackQuery):
        uid = query.from_user.id if query.from_user else 0
        if not _is_admin(uid):
            await query.answer("Sirf admin.", show_alert=True)
            return
        try:
            from handlers.indexing import idx_db
            channels = await idx_db.list_channels()
        except Exception as e:
            channels = []
            logger.warning("list_channels: %s", e)
        await query.answer()
        if not channels:
            await query.edit_message_text(
                "📂 Koi indexing channel set nahi hai.\nPehle <b>Set Indexing Channel</b> karo.",
                reply_markup=kb_idx_menu(),
            )
            return
        rows = []
        for ch in channels:
            cid = int(ch.get("channel_id", 0))
            title = (ch.get("title") or str(cid))[:40]
            rows.append([InlineKeyboardButton(title, callback_data=f"idx_ch:{cid}")])
        rows.append([InlineKeyboardButton("⬅️ Back", callback_data="idx_menu")])
        await query.edit_message_text(
            "▶️ <b>Select channel to index:</b>",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    @bot.on_callback_query(filters.regex(r"^idx_ch:(-?\d+)$"), group=-5)
    async def _idx_ch(client: Client, query: CallbackQuery):
        uid = query.from_user.id if query.from_user else 0
        if not _is_admin(uid):
            await query.answer("Sirf admin.", show_alert=True)
            return
        cid = int(query.matches[0].group(1))
        _STATE[uid] = {"state": "wait_start", "channel_id": cid}
        await query.answer()
        await query.edit_message_text(
            f"▶️ Channel <code>{cid}</code>\n\n"
            "Ab <b>START</b> message ka link ya message ID bhejo\n"
            "(ya us message ko yahan forward karo).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Back", callback_data="idx_list_ch")],
            ]),
        )

    @bot.on_message(filters.private & filters.incoming, group=-5)
    async def _idx_text(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else 0
        if not _is_admin(uid):
            return
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
                    await message.reply_text("Valid channel ID bhejo (jaise -100...) ya forward karo.")
                    return
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
                await message.reply_text(
                    f"✅ Channel set: <b>{title}</b> (<code>{cid}</code>)",
                    reply_markup=kb_idx_menu(),
                )
            except Exception as e:
                await message.reply_text(f"Error: {type(e).__name__}: {e}")
            return
        if state == "wait_start":
            mid = None
            if message.forward_from_chat and message.forward_from_message_id:
                mid = message.forward_from_message_id
                st["channel_id"] = message.forward_from_chat.id
            else:
                import re
                m = re.search(r"/(\d+)/?$", text) or re.search(r"^(\d+)$", text)
                if m:
                    mid = int(m.group(1))
            if not mid:
                await message.reply_text("Start message ID ya link bhejo.")
                return
            st["start_id"] = mid
            st["state"] = "wait_end"
            _STATE[uid] = st
            await message.reply_text(
                f"✅ Start = <code>{mid}</code>\nAb <b>END</b> message ID/link bhejo."
            )
            return
        if state == "wait_end":
            mid = None
            if message.forward_from_chat and message.forward_from_message_id:
                mid = message.forward_from_message_id
            else:
                import re
                m = re.search(r"/(\d+)/?$", text) or re.search(r"^(\d+)$", text)
                if m:
                    mid = int(m.group(1))
            if not mid:
                await message.reply_text("End message ID ya link bhejo.")
                return
            st["end_id"] = mid
            st["state"] = "idle"
            _STATE[uid] = st
            channel_id = st.get("channel_id")
            start_id = st.get("start_id")
            end_id = mid
            status = await message.reply_text(
                f"📥 Indexing start...\nChannel <code>{channel_id}</code>\n"
                f"Range <code>{start_id}</code> → <code>{end_id}</code>"
            )
            try:
                from handlers.indexing.worker import run_indexing
                import asyncio
                asyncio.create_task(run_indexing(
                    client, uid, channel_id, start_id, end_id,
                    message.chat.id, status.id,
                ))
            except Exception as e:
                await status.edit_text(f"❌ Worker error: {type(e).__name__}: {e}")
