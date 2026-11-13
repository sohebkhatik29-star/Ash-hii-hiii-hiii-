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
    "jailear": "jailer",
    "jailaer": "jailer",
    "jailaaer": "jailer",
    "jaailaer": "jailer",
    "jailerr": "jailer",
    "jiler": "jailer",
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


def _norm_title(s: str) -> str:
    """Lowercase, strip year + punctuation for exact spelling compare."""
    s = (s or "").lower()
    s = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def has_exact_tmdb_match(query: str, suggestions: List[Dict[str, Any]]) -> bool:
    """True when user typed the real title (spelling already correct)."""
    nq = _norm_title(query)
    if not nq or len(nq) < 2:
        return False
    for it in suggestions or []:
        nt = _norm_title(it.get("title") or "")
        if nt and nt == nq:
            return True
    return False


def get_exact_match_item(query: str, suggestions: List[Dict[str, Any]]):
    """Return first suggestion whose title exactly matches query (ignore year)."""
    nq = _norm_title(query)
    if not nq or len(nq) < 2:
        return None
    for it in suggestions or []:
        nt = _norm_title(it.get("title") or "")
        if nt and nt == nq:
            return it
    return None


def is_unreleased(item: Dict[str, Any]) -> bool:
    """True if TMDB release_date is strictly after today."""
    from datetime import date
    rd = (item or {}).get("release_date") or ""
    if len(rd) < 10:
        return False
    try:
        y, m, d = int(rd[0:4]), int(rd[5:7]), int(rd[8:10])
        rel = date(y, m, d)
    except Exception:
        return False
    return rel > date.today()


def format_release_date(rd: str) -> str:
    """2026-12-18 -> 18 December 2026"""
    months = [
        "", "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    if not rd or len(rd) < 10:
        return rd or ""
    try:
        y, m, d = int(rd[0:4]), int(rd[5:7]), int(rd[8:10])
        if 1 <= m <= 12:
            return f"{d} {months[m]} {y}"
    except Exception:
        pass
    return rd


def build_unreleased_text(query: str, item: Dict[str, Any]) -> str:
    rd = item.get("release_date") or ""
    nice = format_release_date(rd)
    title = item.get("title") or query
    year = item.get("year") or ""
    label = f"{title}" + (f" ({year})" if year else "")
    return (
        f"⏳ <b>{label}</b>\n\n"
        f"Sorry, ye movie <b>abhi release nahi hui</b>.\n"
        f"Expected release: <b>{nice or 'TBA'}</b>\n\n"
        f"Release hone ke baad hi yahan milegi."
    )


def build_unreleased_keyboard(item: Dict[str, Any]):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    import urllib.parse
    title = item.get("title") or "movie"
    year = item.get("year") or ""
    q = f"{title} {year} release date".strip()
    url = "https://www.google.com/search?q=" + urllib.parse.quote(q)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Check release date", url=url)],
    ])




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


def _single_deletes(word: str) -> List[str]:
    """One-char deletions: Jailear -> Jailer."""
    if len(word) < 4:
        return []
    out = []
    for i in range(len(word)):
        out.append(word[:i] + word[i + 1 :])
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

        # single-char deletions on longest word (Jailear -> Jailer)
        if len(longest) >= 4:
            for dl in _single_deletes(longest.lower())[:8]:
                if len(dl) < 3:
                    continue
                rebuilt = []
                for w in words:
                    if w == longest:
                        rebuilt.append(dl)
                    else:
                        rebuilt.append(_collapse_repeats(w))
                add(" ".join(rebuilt))
            # deletions on collapsed longest
            long_c = _collapse_repeats(longest.lower())
            if long_c != longest.lower():
                for dl in _single_deletes(long_c)[:6]:
                    if len(dl) >= 3:
                        add(dl)

        # prefix of longest word (Jail from Jailear) — TMDB handles partial
        lw = _collapse_repeats(longest.lower())
        if len(lw) >= 5:
            add(lw[:5])
            add(lw[:4])

    # also try first significant word alone (franchise / single-title)
    sig = [w for w in _collapse_repeats(base).split() if len(w) >= 3]
    if len(sig) >= 1:
        add(sig[0])
        if len(sig) >= 2:
            add(" ".join(sig[:2]))

    # attach year variants for ranking later (search without year is broader)
    return ordered[:12]


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
            "release_date": date,  # YYYY-MM-DD or empty
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

    # Keep only titles that really relate to what user typed (kill random "red/cut" noise)
    filtered = []
    q_plain = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q.lower())
    q_plain = re.sub(r"\s+", " ", q_plain).strip()
    q_forms = list(dict.fromkeys([
        q_plain,
        _collapse_repeats(q_plain),
        _collapse_soft(q_plain),
    ]))
    # also single-char deletions of last form (jaata -> jaat)
    base = q_forms[-1] if q_forms else q_plain
    if 4 <= len(base) <= 12 and " " not in base:
        for i in range(len(base)):
            q_forms.append(base[:i] + base[i+1:])
    q_forms = list(dict.fromkeys(q_forms))
    stop = {"to", "of", "the", "a", "an", "in", "on", "and", "for", "with"}

    for it in items:
        ymatch, contain, overlap, pop = _score_against_query(it, q, year_in_q)
        title_l = (it.get("title") or "").lower()
        title_c = _collapse_repeats(title_l)
        title_toks = [w for w in title_c.split() if len(w) >= 2 and w not in stop]
        title_norm = _norm_title(it.get("title") or "")

        real_contain = False
        tok_hit = False
        for qf in q_forms:
            qf = re.sub(r"\s+", " ", qf).strip()
            if not qf:
                continue
            if qf in title_c or title_norm == _norm_title(qf):
                real_contain = True
            q_toks = [w for w in qf.split() if len(w) >= 2 and w not in stop]
            hits = sum(1 for w in q_toks if w in title_c)
            if len(q_toks) >= 2:
                ok = hits >= len(q_toks)
                if ok and len(title_toks) > len(q_toks) + 1 and qf not in title_c:
                    ok = False
                if ok:
                    tok_hit = True
            elif q_toks and hits >= 1:
                # single token: title should be close (not a long unrelated phrase)
                if len(title_toks) <= 3 or title_norm == _norm_title(qf) or qf in title_c:
                    tok_hit = True

        if ymatch or real_contain or tok_hit:
            filtered.append(it)
    if filtered:
        items = filtered
    else:
        items = []
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
