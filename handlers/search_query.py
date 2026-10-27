# ============================================================================
# handlers/search_query.py  — ONLY query clean + title match (alg file)
# - Message se movie name extract (noise hatao)
# - "PUSHPA" → Pushpa The Rise match
# - "2021" alone → NO match
# - "THE RISE" / weak tail words → NO match (not first + short)
# - "PUSHPA 2021" → match
# ============================================================================
import re
from typing import List

# Extra noise beyond DB STOPWORDS — request / chat filler
NOISE = {
    "dedo", "de", "do", "please", "plz", "pls", "pleasee", "bhai", "bro", "sir", "ji",
    "movie", "movies", "film", "films", "full", "hd", "print",
    "kya", "hai", "ka", "ki", "ke", "ko", "se", "me", "mein", "aur", "or",
    "the", "a", "an", "to", "of", "in", "on", "for", "with", "from", "this", "that",
    "send", "bhejo", "bhej", "bhejdo", "bhejde", "chahiye", "chahie", "need", "want",
    "download", "link", "file", "files", "quality", "hindi", "english", "tamil", "telugu",
    "dubbed", "dual", "audio", "ok", "okay", "yaar", "yar", "dost", "friend",
    "group", "bot", "admin", "thanks", "thank", "thx", "tq", "good", "nice",
    "hello", "hi", "hey", "hii", "hlo", "kese", "kaise", "how", "are", "you",
    "kr", "kar", "dena", "mil", "mile", "mila", "available", "he", "h",
    "krdo", "kardo", "mujhe", "mujh", "help", "namaste", "namaskar",
    "dijiye", "kripya", "krupaya", "kindly", "year", "new", "latest", "old",
    "free", "paid", "premium", "yrr", "yar", "rehne", "do", "dedo",
    "as", "asap", "now", "fast", "jaldi", "abhi", "only", "sirf",
}


def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_search_query(raw: str) -> str:
    """
    Message se movie name nikaalo.
    Example: 'hello bhai please PUSHPA THE RISE dedo' → 'pushpa rise'
    Years (19xx/20xx) keep (with name), but year-alone rejected in match.
    """
    text = _norm(raw)
    if not text:
        return ""
    tokens = []
    for t in text.split():
        if not t:
            continue
        if t.isdigit() and len(t) == 4 and t.startswith(("19", "20")):
            tokens.append(t)
            continue
        if t in NOISE:
            continue
        if len(t) < 2:
            continue
        tokens.append(t)
    return " ".join(tokens).strip()


def _title_tokens(title: str) -> List[str]:
    t = _norm(title)
    out = []
    for w in t.split():
        if w in NOISE and not (w.isdigit() and len(w) == 4):
            continue
        if len(w) >= 2:
            out.append(w)
    return out


def is_strict_title_match(query: str, movie_title: str, movie_year=None) -> bool:
    """
    Match rules:
    1) Sirf year (2021) → NEVER match
    2) Query words must hit title; coverage ~60%
    3) At least ONE strong hit:
       - first significant title word (PUSHPA of Pushpa The Rise), OR
       - distinctive token len >= 5 present in title
    4) Weak tail only (RISE, PART) without first/strong word → NO match
    5) If query has year, movie year must match when movie has year
    """
    q = clean_search_query(query)
    if not q:
        return False
    q_tokens = [t for t in q.split() if t]
    if not q_tokens:
        return False

    q_years = {t for t in q_tokens if t.isdigit() and len(t) == 4}
    q_words = [t for t in q_tokens if t not in q_years]

    # Year-only query → reject (user: 2021 alone must not return movies)
    if not q_words:
        return False

    title_toks = _title_tokens(movie_title or "")
    title_words = [t for t in title_toks if not (t.isdigit() and len(t) == 4)]
    if not title_words:
        return False
    title_set = set(title_words)

    # Year filter when user typed a year with the name
    if q_years and movie_year is not None:
        try:
            if str(int(movie_year)) not in q_years:
                return False
        except Exception:
            if str(movie_year) not in q_years:
                return False

    # Word hits
    matched = [t for t in q_words if t in title_set]
    if not matched and len(q_words) == 1 and len(q_words[0]) >= 5:
        tok = q_words[0]
        if any(tok in tw or tw in tok for tw in title_words if len(tw) >= 4):
            matched = [tok]
    if not matched:
        return False

    need = max(1, (len(q_words) * 3 + 4) // 5)
    if len(matched) < need:
        return False

    # Strong hit required: first title word OR any matched token len >= 5
    first = title_words[0]
    strong = False
    for t in matched:
        if t == first:
            strong = True
            break
        if len(t) >= 5:
            strong = True
            break
    if not strong:
        return False

    return True


def filter_strict_matches(query: str, movies: list) -> list:
    """Keep only movies that match cleaned query under strict rules."""
    out = []
    for m in movies or []:
        title = m.get("title") or ""
        year = m.get("year")
        if is_strict_title_match(query, title, year):
            out.append(m)
    return out
