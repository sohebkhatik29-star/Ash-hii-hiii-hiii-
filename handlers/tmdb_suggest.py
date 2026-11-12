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

# very common movie-title typos (lowercase word -> correct)
_COMMON_FIX = {
    "fack": "fake",
    "fakc": "fake",
    "faek": "fake",
    "wif": "wife",
    "wive": "wife",
    "wifes": "wife",
    "mivie": "movie",
    "moive": "movie",
    "movei": "movie",
    "kgf": "kgf",
    "puspa": "pushpa",
    "pushpaa": "pushpa",
    "pushpaaa": "pushpa",
    "jaaat": "jaat",
    "jaata": "jaat",
    "jaatt": "jaat",
    "rrr": "rrr",
}


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


def _collapse_repeats(s: str) -> str:
    """jaaat -> jaat, pushpaa -> pushpa (keep at most 1 of each consecutive)."""
    return re.sub(r"(.)\1+", r"\1", s)


def _collapse_soft(s: str) -> str:
    """allow double letters max: jaaaat -> jaat, book -> book."""
    return re.sub(r"(.)\1{2,}", r"\1\1", s)


def _adj_swaps(word: str) -> List[str]:
    """Adjacent letter swaps: fack -> afck, fcak, fakc."""
    if len(word) < 3:
        return []
    out = []
    chars = list(word)
    for i in range(len(chars) - 1):
        c = chars[:]
        c[i], c[i + 1] = c[i + 1], c[i]
        out.append("".join(c))
    return out


def _apply_common_fix(text: str) -> str:
    parts = []
    for w in text.split():
        lw = w.lower()
        if lw in _COMMON_FIX:
            fixed = _COMMON_FIX[lw]
            # preserve rough casing of first letter
            if w[:1].isupper():
                fixed = fixed[:1].upper() + fixed[1:]
            parts.append(fixed)
        else:
            parts.append(w)
    return " ".join(parts)


def _query_variants(query: str) -> List[str]:
    """Generate a small set of spelling-corrected search queries for TMDB."""
    q = (query or "").strip()
    if not q:
        return []

    year = None
    ym = re.search(r"\b(19\d{2}|20\d{2})\b", q)
    if ym:
        year = ym.group(1)
    base = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q)
    base = re.sub(r"\s+", " ", base).strip() or q

    seen = set()
    ordered: List[str] = []

    def add(s: str):
        s = re.sub(r"\s+", " ", (s or "").strip())
        if not s or len(s) < 2:
            return
        key = s.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(s)

    add(base)
    add(_apply_common_fix(base))
    add(_collapse_repeats(base))
    add(_collapse_soft(base))
    add(_apply_common_fix(_collapse_repeats(base)))

    # per-word: collapse + common fix + one adjacent swap each
    words = base.split()
    if words:
        collapsed_words = [_collapse_repeats(w) for w in words]
        add(" ".join(collapsed_words))
        fixed_words = []
        for w in collapsed_words:
            lw = w.lower()
            fixed_words.append(_COMMON_FIX.get(lw, w))
        add(" ".join(fixed_words))

        # adjacent swaps on the longest word only (limit API noise)
        longest = max(words, key=len)
        if len(longest) >= 3:
            for sw in _adj_swaps(longest.lower())[:4]:
                rebuilt = []
                for w in words:
                    if w == longest:
                        rebuilt.append(sw)
                    else:
                        rebuilt.append(_collapse_repeats(w))
                add(" ".join(rebuilt))

    # also try first significant word alone (franchise / single-title)
    sig = [w for w in _collapse_repeats(base).split() if len(w) >= 3]
    if len(sig) >= 1:
        add(sig[0])
        if len(sig) >= 2:
            add(" ".join(sig[:2]))

    # attach year variants for ranking later (search without year is broader)
    return ordered[:8]


async def _tmdb_raw(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    key = (getattr(Config, "TMDB_API_KEY", None) or "").strip()
    q = (query or "").strip()
    if not key or not q or len(q) < 2:
        return []

    params = {
        "api_key": key,
        "query": q,
        "include_adult": "false",
        "language": "en-US",
        "page": "1",
    }
    url = "https://api.themoviedb.org/3/search/multi?" + urllib.parse.urlencode(params)
    try:
        data = await asyncio.to_thread(_http_get_json, url)
    except Exception as e:
        logger.warning("tmdb_raw(%s) failed: %s", q, e)
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
        if len(out) >= limit:
            break
    return out


def _score_against_query(item: Dict[str, Any], query: str, year_in_q) -> tuple:
    """Higher is better — prefer titles close to user query."""
    title = (item.get("title") or "").lower()
    q = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", (query or "").lower())
    q = re.sub(r"\s+", " ", q).strip()
    q_c = _collapse_repeats(q)
    t_c = _collapse_repeats(title)

    ymatch = 0
    if year_in_q and item.get("year") == year_in_q:
        ymatch = 3
    elif year_in_q and item.get("year"):
        try:
            if abs(int(item["year"]) - int(year_in_q)) <= 2:
                ymatch = 1
        except Exception:
            pass

    # token overlap after collapse
    q_toks = set(q_c.split())
    t_toks = set(t_c.split())
    overlap = len(q_toks & t_toks)

    # prefix / containment
    contain = 0
    if q_c and (q_c in t_c or t_c in q_c):
        contain = 2
    elif q_c and t_c.startswith(q_c[: max(3, len(q_c) // 2)]):
        contain = 1

    return (ymatch, contain, overlap, item.get("popularity", 0))


async def tmdb_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search TMDB with spelling-tolerant variants.
    Returns [{title, year, tmdb_id, media_type, label}].
    """
    q = (query or "").strip()
    if not q or len(q) < 2:
        return []

    year_in_q = None
    m = re.search(r"\b(19\d{2}|20\d{2})\b", q)
    if m:
        year_in_q = m.group(1)

    variants = _query_variants(q)
    merged: Dict[tuple, Dict[str, Any]] = {}

    for i, variant in enumerate(variants):
        # stop early if we already have solid hits from first variant(s)
        if i >= 2 and len(merged) >= limit:
            break
        rows = await _tmdb_raw(variant, limit=10)
        for row in rows:
            key = (row["title"].lower(), row.get("year") or "")
            if key not in merged:
                merged[key] = row

    items = list(merged.values())
    items.sort(key=lambda it: _score_against_query(it, q, year_in_q), reverse=True)

    # Keep only titles that relate to what user typed (avoid random TMDB noise)
    filtered = []
    for it in items:
        ymatch, contain, overlap, pop = _score_against_query(it, q, year_in_q)
        title_l = (it.get("title") or "").lower()
        q_plain = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q.lower())
        q_plain = re.sub(r"\s+", " ", q_plain).strip()
        q_c = _collapse_repeats(q_plain)
        q_toks = [w for w in q_c.split() if len(w) >= 3]
        title_c = _collapse_repeats(title_l)
        tok_hit = any(w in title_c for w in q_toks)
        if ymatch or contain or overlap or tok_hit:
            filtered.append(it)
    if filtered:
        items = filtered
    return items[:limit]


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
