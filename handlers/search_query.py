# ============================================================================
# handlers/search_query.py  — ONLY query clean + title match (alg file)
#
# Rules:
# 1) Year-only → never match
# 2) All query words appear in title → match
#    "LITTLE KRISHNA" → Little Krishna ...   (NOT Little Thomas)
# 3) All title words appear in query → match (short titles)
# 4) Single distinctive FIRST word (len >= 5) → match franchise
#    "PUSHPA" → Pushpa The Rise
#    "FURY" (len 4) → NO match on Fury In The Shadows
# 5) Weak tail-only (RISE, SHADOWS without first word) → NO match
# ============================================================================
import re
from typing import List

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
    """Noise hatao; years name ke saath rakho."""
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
    Hybrid smart match:
    - All query words in title → OK  (LITTLE KRISHNA → Little Krishna, not Little Thomas)
    - All title words in query → OK  (short exact titles)
    - Query = single distinctive first word (len >= 5) → OK franchise (PUSHPA)
    - Year-only / weak tail-only → NO
    """
    q = clean_search_query(query)
    if not q:
        return False
    q_tokens = [t for t in q.split() if t]
    if not q_tokens:
        return False

    q_years = {t for t in q_tokens if t.isdigit() and len(t) == 4}
    q_words = [t for t in q_tokens if t not in q_years]

    if not q_words:
        return False  # year-only

    title_toks = _title_tokens(movie_title or "")
    title_words = [t for t in title_toks if not (t.isdigit() and len(t) == 4)]
    if not title_words:
        return False

    q_set = set(q_words)
    title_set = set(title_words)

    # Year constraint when user typed a year
    if q_years and movie_year is not None:
        try:
            if str(int(movie_year)) not in q_years:
                return False
        except Exception:
            if str(movie_year) not in q_years:
                return False

    # Path A: every query word appears in the title
    # "LITTLE KRISHNA" matches "Little Krishna Movie 1" but NOT "Little Thomas"
    if all(qw in title_set for qw in q_words):
        return True

    # Path B: every significant title word is in the query (query covers full title)
    if all(tw in q_set for tw in title_words):
        return True

    # Path C: single-word franchise query — first title word only, len >= 5
    # "PUSHPA" → Pushpa The Rise  |  "FURY" (len 4) → blocked
    if len(q_words) == 1:
        first = title_words[0]
        if len(first) >= 5 and first == q_words[0]:
            return True

    return False


def filter_strict_matches(query: str, movies: list) -> list:
    out = []
    for m in movies or []:
        title = m.get("title") or ""
        year = m.get("year")
        if is_strict_title_match(query, title, year):
            out.append(m)
    return out
