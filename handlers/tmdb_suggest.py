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



_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _omdb_released_to_iso(released: str) -> str:
    """OMDb '18 Dec 2026' / 'N/A' -> '2026-12-18' or ''."""
    s = (released or "").strip()
    if not s or s.upper() == "N/A":
        return ""
    parts = s.replace(",", " ").split()
    if len(parts) == 3 and parts[0].isdigit() and parts[2].isdigit():
        try:
            d = int(parts[0])
            m = _MONTH_MAP.get(parts[1][:3].lower(), 0)
            y = int(parts[2])
            if m and 1 <= d <= 31:
                return f"{y:04d}-{m:02d}-{d:02d}"
        except Exception:
            return ""
    if len(s) >= 4 and s[:4].isdigit():
        return s[:4] + "-01-01"
    return ""


async def _omdb_by_title(query: str) -> Dict[str, Any] | None:
    """Exact-ish title lookup on OMDb (IMDb data)."""
    key = (getattr(Config, "OMDB_API_KEY", None) or "").strip()
    q = (query or "").strip()
    if not key or not q or len(q) < 2:
        return None
    # strip trailing year for ?t= then re-check
    q_no_year = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q)
    q_no_year = re.sub(r"\s+", " ", q_no_year).strip() or q
    year_m = re.search(r"\b(19\d{2}|20\d{2})\b", q)
    year = year_m.group(1) if year_m else ""

    params = {"apikey": key, "t": q_no_year, "type": "movie"}
    if year:
        params["y"] = year
    url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(params)
    try:
        data = await asyncio.to_thread(_http_get_json, url)
    except Exception as e:
        logger.warning("omdb_by_title(%s) failed: %s", q, e)
        return None
    if not data or str(data.get("Response", "")).lower() != "true":
        # retry without year
        if year:
            params.pop("y", None)
            url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(params)
            try:
                data = await asyncio.to_thread(_http_get_json, url)
            except Exception:
                return None
            if not data or str(data.get("Response", "")).lower() != "true":
                return None
        else:
            return None
    title = (data.get("Title") or "").strip()
    if not title:
        return None
    y = (data.get("Year") or "")[:4]
    if y and not y.isdigit():
        y = ""
    rd = _omdb_released_to_iso(data.get("Released") or "")
    if not rd and y:
        rd = f"{y}-01-01"
    label = f"{title}" + (f" ({y})" if y else "")
    return {
        "title": title,
        "year": y,
        "release_date": rd,
        "tmdb_id": None,
        "imdb_id": data.get("imdbID") or "",
        "media_type": "movie",
        "label": label,
        "popularity": 0.0,
        "source": "omdb",
    }


async def _omdb_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """OMDb search list + enrich top hits with full Released date."""
    key = (getattr(Config, "OMDB_API_KEY", None) or "").strip()
    q = (query or "").strip()
    if not key or not q or len(q) < 2:
        return []
    q_no_year = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", q)
    q_no_year = re.sub(r"\s+", " ", q_no_year).strip() or q
    params = {"apikey": key, "s": q_no_year, "type": "movie"}
    url = "https://www.omdbapi.com/?" + urllib.parse.urlencode(params)
    try:
        data = await asyncio.to_thread(_http_get_json, url)
    except Exception as e:
        logger.warning("omdb_search(%s) failed: %s", q, e)
        return []
    if not data or str(data.get("Response", "")).lower() != "true":
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in (data.get("Search") or [])[: limit + 2]:
        title = (r.get("Title") or "").strip()
        y = (r.get("Year") or "")[:4]
        if y and not y.isdigit():
            y = ""
        if not title:
            continue
        key_s = (title.lower(), y)
        if key_s in seen:
            continue
        seen.add(key_s)
        # enrich with full plot endpoint for Released
        imdb = r.get("imdbID") or ""
        rd = f"{y}-01-01" if y else ""
        if imdb:
            try:
                u2 = "https://www.omdbapi.com/?" + urllib.parse.urlencode(
                    {"apikey": key, "i": imdb}
                )
                det = await asyncio.to_thread(_http_get_json, u2)
                if det and str(det.get("Response", "")).lower() == "true":
                    rd2 = _omdb_released_to_iso(det.get("Released") or "")
                    if rd2:
                        rd = rd2
                    if not y:
                        y2 = (det.get("Year") or "")[:4]
                        if y2.isdigit():
                            y = y2
            except Exception:
                pass
        label = f"{title}" + (f" ({y})" if y else "")
        out.append({
            "title": title,
            "year": y,
            "release_date": rd,
            "tmdb_id": None,
            "imdb_id": imdb,
            "media_type": "movie",
            "label": label,
            "popularity": 0.0,
            "source": "omdb",
        })
        if len(out) >= limit:
            break
    return out



async def _gemini_movie_guess(query: str) -> Dict[str, Any] | None:
    """Last-resort: Gemini fixes spelling / release status when TMDB+OMDb miss."""
    key = (getattr(Config, "GEMINI_API_KEY", None) or "").strip()
    q = (query or "").strip()
    if not key or not q or len(q) < 2:
        return None
    from datetime import date
    today = date.today().isoformat()
    prompt = (
        "Telegram movie-search bot helper. Today: " + today + ".\n"
        "User typed (possibly misspelled) movie/TV title. Fix spelling using common "
        "Indian/Hollywood film names.\n"
        "Reply ONLY JSON object, no markdown:\n"
        '{"correct_title":"...","year":"YYYY or","released":true,'
        '"release_date":"YYYY-MM-DD or","confidence":0.0,"is_real_movie":true}\n'
        "Examples of fixes:\n"
        "Jailear -> Jailer (2023)\n"
        "Jaata -> Jaat (2025)\n"
        "Fack wife -> My Fake Wife\n"
        "Puspa -> Pushpa\n"
        "If clearly an upcoming film, set released=false and best known release_date.\n"
        "If unknown, is_real_movie=false confidence<=0.3\n"
        "User: " + q
    )
    model = "gemini-3.5-flash-lite"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        + model
        + ":generateContent?key="
        + urllib.parse.quote(key)
    )
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 200},
    }).encode()

    def _post():
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    try:
        data = await asyncio.to_thread(_post)
    except Exception as e:
        logger.warning("gemini_movie_guess(%s) failed: %s", q, e)
        return None
    try:
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text")
            or ""
        )
    except Exception:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    conf = float(obj.get("confidence") or 0)
    is_real = bool(obj.get("is_real_movie"))
    title = (obj.get("correct_title") or "").strip()
    if not title or conf < 0.45 or not is_real:
        return None
    year = str(obj.get("year") or "").strip()
    if year and not re.match(r"^(19|20)\d{2}$", year):
        year = ""
    rd = str(obj.get("release_date") or "").strip()
    if rd.lower() in ("null", "none", "n/a", ""):
        rd = ""
    if rd and not re.match(r"^\d{4}-\d{2}-\d{2}$", rd):
        # try year only
        if year:
            rd = f"{year}-01-01"
        else:
            rd = ""
    if not rd and year:
        rd = f"{year}-01-01"
    # if model says not released, ensure release_date is in future when possible
    released = obj.get("released")
    if released is False and rd:
        pass  # keep date for is_unreleased()
    label = f"{title}" + (f" ({year})" if year else "")
    return {
        "title": title,
        "year": year,
        "release_date": rd,
        "tmdb_id": None,
        "imdb_id": "",
        "media_type": "movie",
        "label": label,
        "popularity": conf * 10,
        "source": "gemini",
        "confidence": conf,
    }


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

    # OMDb (IMDb) fallback — when TMDB has no solid hits or no exact title match
    has_exact = any(_norm_title(it.get("title") or "") == _norm_title(q) for it in items)
    if not items or not has_exact:
        try:
            omdb_exact = await _omdb_by_title(q)
        except Exception as e:
            logger.warning("omdb exact fallback: %s", e)
            omdb_exact = None
        omdb_rows: List[Dict[str, Any]] = []
        if omdb_exact:
            omdb_rows.append(omdb_exact)
        if len(omdb_rows) < limit:
            try:
                more = await _omdb_search(q, limit=limit)
            except Exception as e:
                logger.warning("omdb search fallback: %s", e)
                more = []
            for row in more:
                key = (row["title"].lower(), row.get("year") or "")
                if not any(
                    (x["title"].lower(), x.get("year") or "") == key for x in omdb_rows + items
                ):
                    omdb_rows.append(row)
        # apply same relevance filter spirit on OMDb rows (reuse score)
        omdb_kept = []
        for it in omdb_rows:
            ymatch, contain, overlap, pop = _score_against_query(it, q, year_in_q)
            title_l = (it.get("title") or "").lower()
            title_c = _collapse_repeats(title_l)
            title_norm = _norm_title(it.get("title") or "")
            ok = False
            for qf in q_forms:
                qf = re.sub(r"\s+", " ", qf).strip()
                if not qf:
                    continue
                if qf in title_c or title_norm == _norm_title(qf):
                    ok = True
                    break
                q_toks = [w for w in qf.split() if len(w) >= 2 and w not in stop]
                hits = sum(1 for w in q_toks if w in title_c)
                if len(q_toks) >= 2 and hits >= len(q_toks):
                    ok = True
                    break
                if len(q_toks) == 1 and hits >= 1 and len(title_c.split()) <= 4:
                    ok = True
                    break
            if ok or ymatch:
                omdb_kept.append(it)
        # merge: exact OMDb first, then existing TMDB, then other OMDb
        merged_out: List[Dict[str, Any]] = []
        seen_k = set()
        for it in omdb_kept + items:
            k = (it["title"].lower(), it.get("year") or "")
            if k in seen_k:
                continue
            seen_k.add(k)
            merged_out.append(it)
        items = merged_out

    # Gemini last resort — only if still no solid exact title
    has_exact = any(_norm_title(it.get("title") or "") == _norm_title(q) for it in items)
    if not items or not has_exact:
        try:
            g = await _gemini_movie_guess(q)
        except Exception as e:
            logger.warning("gemini fallback: %s", e)
            g = None
        if g:
            gk = (g["title"].lower(), g.get("year") or "")
            # put Gemini hit first if not already present
            if not any((it["title"].lower(), it.get("year") or "") == gk for it in items):
                items = [g] + list(items)
            else:
                # upgrade release_date if missing on existing
                for it in items:
                    if (it["title"].lower(), it.get("year") or "") == gk:
                        if not it.get("release_date") and g.get("release_date"):
                            it["release_date"] = g["release_date"]
                        break

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
