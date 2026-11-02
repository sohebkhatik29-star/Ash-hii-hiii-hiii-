# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/admin_commands.py
# Purpose: Dedicated Admin Management Commands: /movies, /delete, /stats,
#          /status, and manual poster photo updates.
# ==============================================================================

import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from configs import Config
from handlers.database import db
from handlers.session_manager import session_mgr
from handlers.broadcast_handlers import main_broadcast_handler

logger = logging.getLogger(__name__)

def register_admin_handlers(bot: Client):

    @bot.on_message(filters.command("status") & filters.private)
    async def status_handler(client: Client, cmd: Message):
        """View active session state and saved qualities."""
        admin_id = cmd.from_user.id
        if admin_id not in Config.ADMINS:
            return

        session = await session_mgr.get_session(admin_id)
        if not session or not session.get("title"):
            await cmd.reply_text(
                "ℹ️ <b>Koi Active Movie Session Nahi Hai.</b>\n\n"
                "Nayi movie start karne ke liye <b>/addmovie</b> bhejein.",
                quote=True
            )
            return

        movie = await db.get_movie(session.get("movie_id"))
        qualities = movie.get("qualities", {}) if movie else {}
        q_list = [f"• <b>{q_name}:</b> {q_val.get('file_size_formatted', 'N/A')}" for q_name, q_val in qualities.items()]
        q_str = "\n".join(q_list) if q_list else "None yet"

        await cmd.reply_text(
            f"📊 <b>CURRENT ACTIVE MOVIE STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🍿 <b>Movie:</b> <code>{session.get('title')} ({session.get('year', '')})</code>\n"
            f"🆔 <b>Movie ID:</b> <code>{session.get('movie_id')}</code>\n\n"
            f"📁 <b>Saved Qualities:</b>\n{q_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 Finish karne ke liye <b>/finish</b> ya cancel ke liye <b>/cancel</b> dabayein.",
            quote=True
        )

    @bot.on_message(filters.command("movies") & filters.private)
    async def list_movies_handler(client: Client, cmd: Message):
        """List recent movies with quick links."""
        admin_id = cmd.from_user.id
        if admin_id not in Config.ADMINS:
            return

        movies = await db.list_movies(limit=10, skip=0)
        total = await db.total_movies_count()

        if not movies:
            await cmd.reply_text("Movie database abhi khali hai.", quote=True)
            return

        text = f"📚 <b>Movie Library ({len(movies)}/{total} Movies):</b>\n\n"
        buttons = []
        for m in movies:
            m_id = m.get("movie_id")
            m_title = m.get("title")
            m_qualities = list(m.get("qualities", {}).keys())
            text += f"• <b>{m_title}</b> [{', '.join(m_qualities)}]\n  ID: <code>{m_id}</code>\n\n"
            buttons.append([InlineKeyboardButton(f"🎬 {m_title}", callback_data=f"preview_mov_{m_id}")])

        await cmd.reply_text(text, quote=True, reply_markup=InlineKeyboardMarkup(buttons))

    @bot.on_message(filters.command("delete") & filters.private)
    async def delete_movie_cmd(client: Client, cmd: Message):
        """Delete a movie by movie_id: /delete <movie_id>"""
        admin_id = cmd.from_user.id
        if admin_id not in Config.ADMINS:
            return

        parts = cmd.text.split(" ", 1)
        if len(parts) < 2:
            await cmd.reply_text("Usage: <code>/delete &lt;movie_id&gt;</code>", quote=True)
            return

        target_id = parts[1].strip()
        ok = await db.delete_movie(target_id)
        if ok:
            await cmd.reply_text(f"✅ Movie <code>{target_id}</code> database se delete ho gayi.", quote=True)
        else:
            await cmd.reply_text(f"❌ Movie ID <code>{target_id}</code> nahi mili.", quote=True)

    @bot.on_message(filters.command("stats") & filters.private)
    async def stats_cmd(client: Client, cmd: Message):
        """View bot metrics."""
        admin_id = cmd.from_user.id
        if admin_id not in Config.ADMINS:
            return

        total_users = await db.total_users_count()
        total_movies = await db.total_movies_count()
        active_sessions = len(session_mgr.active_sessions)

        await cmd.reply_text(
            f"📊 <b>{Config.BRAND_NAME} System Statistics:</b>\n\n"
            f"👥 <b>Total Users:</b> <code>{total_users}</code>\n"
            f"🎬 <b>Total Movies:</b> <code>{total_movies}</code>\n"
            f"⚡ <b>Active Sessions:</b> <code>{active_sessions}</code>\n"
            f"🤖 <b>Bot Status:</b> <code>Online & Operational</code>",
            quote=True
        )

    @bot.on_message(filters.command("broadcast") & filters.private)
    async def broadcast_cmd(client: Client, cmd: Message):
        """Broadcast functionality."""
        if cmd.from_user.id not in Config.ADMINS:
            return
        await main_broadcast_handler(client, cmd)

    @bot.on_message(filters.photo & filters.private)
    async def handle_photo_upload(client: Client, message: Message):
        """Manual poster attach/change."""
        admin_id = message.from_user.id
        if admin_id not in Config.ADMINS:
            return

        session = await session_mgr.get_session(admin_id)
        photo_id = message.photo.file_id

        if session and session.get("movie_id"):
            movie_id = session.get("movie_id")
            await db.update_movie_poster(movie_id, photo_id, source="manual")
            session["poster_file_id"] = photo_id
            await db.set_active_session(admin_id, session)
            await message.reply_text(
                f"🖼 <b>Poster Updated!</b>\n\n"
                f"Yeh poster active movie <b>'{session.get('title')}'</b> ke liye set kar diya gaya hai.",
                quote=True
            )
        else:
            await message.reply_text(
                "🖼 Photo received. Poster set karne ke liye pehle <b>/addmovie</b> se session shuru karein.",
                quote=True
            )


    @bot.on_message(filters.private & filters.text & ~filters.command([
        "start", "status", "movies", "delete", "stats", "addmovie", "newmovie",
        "finish", "done_upload", "cancel", "broadcast", "users",
    ]))
    async def session_edit_title_handler(client: Client, message: Message):
        """Edit Name button ke baad admin naya title bhejta hai."""
        admin_id = message.from_user.id if message.from_user else 0
        if admin_id not in getattr(Config, "ADMINS", set()):
            return
        session = await session_mgr.get_session(admin_id)
        if not session or not session.get("awaiting_edit"):
            return

        from handlers.parser import sanitize_text, normalize_title, clean_movie_title
        import re as _re
        raw = sanitize_text((message.text or "").strip())
        if not raw or len(raw) < 2:
            await message.reply_text(
                "Valid movie name bhejo. Example: <code>Ant-Man and the Wasp 2018</code>",
                quote=True,
            )
            return

        year = None
        ym = _re.search(r"\b(19\d{2}|20\d{2})\b", raw)
        if ym:
            try:
                year = int(ym.group(1))
            except Exception:
                year = None
        title = clean_movie_title(raw, year)
        if not title or title.lower() == "untitled movie":
            title = raw
            if year:
                title = _re.sub(r"\b" + str(year) + r"\b", "", title).strip(" -_.")

        session["title"] = title
        if year:
            session["year"] = year
        session["normalized_title"] = normalize_title(title)
        session["awaiting_edit"] = False
        session_mgr.active_sessions[admin_id] = session
        try:
            await db.set_active_session(admin_id, session)
        except Exception:
            pass

        movie_id = session.get("movie_id")
        if movie_id:
            try:
                upd = {"title": title, "normalized_title": normalize_title(title)}
                if year:
                    upd["year"] = year
                if getattr(db, "movies_col", None) is not None:
                    await db.movies_col.update_one({"movie_id": movie_id}, {"$set": upd})
                if movie_id in getattr(db, "_mem_movies", {}):
                    db._mem_movies[movie_id].update(upd)
            except Exception as e:
                logger.error("edit title db: %s", e)

        year_str = f" ({session.get('year')})" if session.get("year") else ""
        q_str = ", ".join(session.get("qualities") or []) or "—"
        status_text = (
            f"Movie Session Active (v3.3)\n\n"
            f"Title: `{title}`{year_str}\n"
            f"Year: `{session.get('year') or 'N/A'}`\n\n"
            f"All qualities: `{q_str}`\n\n"
            f"✅ Name update ho gaya. Aur quality bhejo ya Generate Links dabaao."
        )
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Continue Upload", callback_data="session_continue"),
                InlineKeyboardButton("Generate Links", callback_data="session_generate"),
            ],
            [
                InlineKeyboardButton("✏️ Edit Name", callback_data="session_edit"),
                InlineKeyboardButton("Cancel Session", callback_data="session_cancel"),
            ],
        ])
        try:
            sid = session.get("status_msg_id")
            cid = session.get("status_chat_id") or message.chat.id
            if sid:
                await client.edit_message_text(cid, sid, status_text, reply_markup=markup)
        except Exception:
            try:
                sent = await message.reply_text(status_text, quote=True, reply_markup=markup)
                session["status_msg_id"] = sent.id
                session["status_chat_id"] = message.chat.id
                session_mgr.active_sessions[admin_id] = session
            except Exception:
                pass
        await message.reply_text(
            f"✅ Title set: <b>{title}{year_str}</b>\nAb Generate Links dabao — yahi name group me jayega.",
            quote=True,
        )
