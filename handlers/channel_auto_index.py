# handlers/channel_auto_index.py
# Auto-index watch channel - full logic inside register to survive push limits
from __future__ import annotations
import asyncio, logging, re
from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from configs import Config
from handlers.database import db
from handlers.parser import extract_movie_metadata

logger = logging.getLogger("ChannelAutoIndex")
WATCH = int(__import__("os").environ.get("AUTO_INDEX_CHANNEL_ID") or getattr(Config, "AUTO_INDEX_CHANNEL_ID", None) or "-1004397160844")
SETTLE = 15
_pending = {}
_stats = {"seen": 0, "saved": 0, "posted": 0, "errors": 0}
_seen = {}

def register_channel_auto_index(bot: Client):
    def _clean(raw):
        if not raw: return ""
        t = re.sub(r"@\w+", " ", str(raw))
        t = re.sub(r"(?i)\b(toonworld4all|toonworld|movies_\d+|ash[_\s]?movies?|movies[_\s]?hub|cine[_\s]?hub|netflix_\d+|filmcity|mirror[_\s]?leech|movies[_\s]?1782|480p|720p|1080p|2160p|4k|hdrip|web[- ]?dl|bluray|x264|x265|hevc|aac|esub|dual\s*audio|mkv|mp4|amzn|nf|hindi|english)\b", " ", t)
        t = re.sub(r"[\[\]\{\}|_()]+\", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _q(fn, cap):
        b = f"{fn or ''} {cap or ''}"
        for p, l in [(r"(?i)2160p|4k", "2160p"), (r"(?i)1080p", "1080p"), (r"(?i)720p", "720p"), (r"(?i)480p", "480p")]:
            if re.search(p, b): return l
        return "HD"

    async def _notify(text):
        for t in filter(None, [getattr(Config, "LOG_CHANNEL", None), getattr(Config, "BOT_OWNER", None)]):
            try:
                await bot.send_message(int(t), str(text)[:3000], disable_web_page_preview=True)
                return
            except: pass

    async def _copy(msg):
        media = msg.document or msg.video
        if not media: return None
        fname = getattr(media, "file_name", None) or ""
        c = (msg.caption or fname or "").strip()[:1000]
        orig = getattr(media, "file_id", None)
        dbch = getattr(Config, "DB_CHANNEL", None)
        if not dbch: return orig
        try:
            sent = await msg.copy(int(dbch), caption=c or None)
            m = sent.document or sent.video
            return getattr(m, "file_id", None) or orig
        except FloodWait as e:
            await asyncio.sleep(int(getattr(e, "value", 5)) + 1)
            try:
                sent = await msg.copy(int(dbch), caption=c or None)
                m = sent.document or sent.video
                return getattr(m, "file_id", None) or orig
            except: return orig
        except: return orig

    async def _getmid(title, year):
        title = _clean(title) or "Unknown"
        yv = int(year) if year and str(year).isdigit() else None
        try:
            if hasattr(db, "find_movie_by_title_year"):
                ex = await db.find_movie_by_title_year(title, year=yv)
                if ex and ex.get("movie_id"): return ex["movie_id"]
            elif hasattr(db, "find_movie_by_title"):
                ex = await db.find_movie_by_title(title, year=yv)
                if ex and ex.get("movie_id"): return ex["movie_id"]
        except: pass
        try:
            doc = await db.create_movie(title=title, normalized_title=title.lower(), year=yv, languages=["Hindi"])
            return doc.get("movie_id") if doc else None
        except: return None

    async def _post(mid, title, qualities):
        target = getattr(Config, "MOVIE_GROUP_ID", None)
        if not target: return
        target = int(target)
        qs = sorted(set(q.get("quality", "HD") for q in qualities)) or ["HD"]
        caption = f"🎬 <b>{title}</b>\n\n📺 {' • '.join(qs)}"
        try:
            btns = [[InlineKeyboardButton(f"⬇️ {q.get('quality','HD')}", callback_data=f"getfile|{mid}|{q.get('quality','HD')}")] for q in qualities if q.get("file_id")]
            mk = InlineKeyboardMarkup(btns) if btns else None
            sent = await bot.send_message(target, caption, reply_markup=mk)
            if hasattr(db, "update_group_post"): await db.update_group_post(mid, target, sent.id)
            await _notify(f"✅ Auto-index POSTED\n{title}\n{' • '.join(qs)}\nhttps://t.me/c/{str(target)[4:]}/{sent.id}")
            _stats["posted"] += 1
        except Exception as e:
            await _notify(f"post fail: {e}")

    async def _fin(mkey, mid):
        await asyncio.sleep(SETTLE)
        try:
            movie = await db.get_movie(mid) if hasattr(db, "get_movie") else None
            if movie: await _post(mid, movie.get("title") or mkey, movie.get("qualities") or [])
        except: _stats["errors"] += 1

    def _sched(mkey, mid):
        old = _pending.get(mkey)
        if old and not old.done(): old.cancel()
        _pending[mkey] = asyncio.create_task(_fin(mkey, mid))

    async def _handle(msg):
        try:
            if msg.id in _seen: return
            _seen[msg.id] = 1
            if len(_seen) > 300:
                for k in list(_seen)[:50]: _seen.pop(k, None)
            media = msg.document or msg.video
            if not media: return
            _stats["seen"] += 1
            fname = getattr(media, "file_name", None) or ""
            cap = msg.caption or ""
            meta = {}
            try: meta = extract_movie_metadata(fname, cap) or {}
            except: pass
            title = _clean(meta.get("title") or fname or "Unknown")
            year = meta.get("year")
            q = _q(fname, cap)
            fid = await _copy(msg)
            if not fid: return
            mid = await _getmid(title, year)
            if not mid: return
            try:
                if hasattr(db, "add_quality"):
                    await db.add_quality(mid, q, fid, size=getattr(media, "file_size", 0), file_name=fname)
                elif hasattr(db, "movies_col"):
                    await db.movies_col.update_one({"movie_id": mid}, {"$addToSet": {"qualities": {"quality": q, "file_id": fid, "file_name": fname}}}, upsert=True)
                _stats["saved"] += 1
                await _notify(f"auto_index SAVED\n{title}\n{q}")
            except Exception as e: logger.error("save %s", e)
            _sched(f"{title.lower()}|{year or ''}", mid)
        except Exception as e:
            logger.error("h %s", e)
            _stats["errors"] += 1

    @bot.on_message(filters.chat(WATCH) & (filters.document | filters.video), group=50)
    async def _on(c, m):
        await _handle(m)

    @bot.on_message(filters.command("autoindex") & filters.private, group=1)
    async def _st(c, m):
        await m.reply_text(f"📡 Auto-Index ON\nWatch:{WATCH}\nSettle:{SETTLE}s\nSeen:{_stats['seen']} Saved:{_stats['saved']} Posted:{_stats['posted']}", quote=True)

    logger.info("Channel auto-index ON %s settle=%s", WATCH, SETTLE)
