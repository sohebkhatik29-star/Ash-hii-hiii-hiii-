# ==============================================================================
# TMDB suggestions — wrong spelling / wrong year → correct options
# ==============================================================================
import asyncio
import json
import logging
import re
import secrets
import time
import urllib.parse
import urllib.request
from typing import List, Dict, Any

from configs import Config

logger = logging.getLogger("TMDBSuggest")

_SUGGEST_CACHE: Dict[str, Dict[str, Any]] = {}
_SUGGEST_TTL = 600


def _purge():
    now = time.time()
    for k, v in list(_SUGGEST_CACHE.items()):
        if now - v.get("ts", 0) > _SUGGEST_TTL:
            _SUGGEST_CACHE.pop(k, None)


def store_suggestions(items: List[Dict[str, Any]], meta=None) -> str:
    _purge()
    cid = secrets.token_hex(3)
    entry: Dict[str, Any] = {"items": items, "ts": time.time()}
    if meta:
        entry["meta"] = meta
    _SUGGEST_CACHE[cid] = entry
    return cid


def get_suggestion(cache_id: str, index: int):
    entry = _SUGGEST_CACHE.get(cache_id) or {}
    items = entry.get("items") or []
    if 0 <= index < len(items):
        return items[index]
    return None


def get_suggest_meta(cache_id: str) -> Dict[str, Any]:
    entry = _SUGGEST_CACHE.get(cache_id) or {}
    return entry.get("meta") or {}


def _http_get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AshMovieBot/1.0", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


async def tmdb_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Search TMDB movie/TV. Returns [{title, year, tmdb_id, media_type, label}]."""
    key = (getattr(Config, "TMDB_API_KEY", None) or "").strip()
    q = (query or "").strip()
    if not key or not q or len(q) < 2:
        return []

    year_in_q = None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", q)
    if m:
        year_in_q = m.group(1)
    q_clean = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q)
    q_clean = re.sub(r"\s+", " ", q_clean).strip() or q

    params = {
        "api_key": key,
        "query": q_clean,
        "include_adult": "false",
        "language": "en-US",
        "page": "1",
    }
    url = "https://api.themoviedb.org/3/search/multi?" + urllib.parse.urlencode(params)

    try:
        data = await asyncio.to_thread(_http_get_json, url)
    except Exception as e:
        logger.warning("tmdb_search failed: %s", e)
        return []

    results = data.get("results") or []
    out: List[Dict[str, Any]] = []
    seen = set()

    for r in results:
        media = r.get("media_type") or ""
        if media not in ("movie", "tv"):
            continue
        if media == "movie":
            title = (r.get("title") or r.get("original_title") or "").strip()
            date = r.get("release_date") or ""
        else:
            title = (r.get("name") or r.get("original_name") or "").strip()
            date = r.get("first_air_date") or ""
        if not title:
            continue
        year = ""
        if date and len(date) >= 4 and date[:4].isdigit():
            year = date[:4]
        key_s = (title.lower(), year)
        if key_s in seen:
            continue
        seen.add(key_s)
        label = f"{title}" + (f" ({year})" if year else "")
        out.append({
            "title": title,
            "year": year,
            "tmdb_id": r.get("id"),
            "media_type": media,
            "label": label,
            "popularity": float(r.get("popularity") or 0),
        })
        if len(out) >= max(limit * 3, 15):
            break

    def sort_key(item):
        ymatch = 0
        if year_in_q and item.get("year") == year_in_q:
            ymatch = 2
        elif year_in_q and item.get("year"):
            try:
                if abs(int(item["year"]) - int(year_in_q)) <= 2:
                    ymatch = 1
            except Exception:
                pass
        return (-ymatch, -item.get("popularity", 0))

    out.sort(key=sort_key)
    return out[:limit]


def build_suggest_text(query: str) -> str:
    return (
        f"🔍 <b>{query}</b>\n\n"
        f"‼️ <b>SPELLING / YEAR CHECK</b>\n"
        f"😊 Sahi option choose karo 👇\n\n"
        f"👇 Neeche diye options me se movie ka sahi naam select karo"
    )


def build_suggest_keyboard(cache_id: str, items: List[Dict[str, Any]]):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for i, it in enumerate(items):
        label = (it.get("label") or it.get("title") or "Movie")[:60]
        rows.append([InlineKeyboardButton(label, callback_data=f"ts|{cache_id}|{i}")])
    rows.append([InlineKeyboardButton("❌ CLOSE ❌", callback_data="ts_close")])
    return InlineKeyboardMarkup(rows)
