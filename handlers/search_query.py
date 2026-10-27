# ============================================================================
# handlers/search_query.py  — ONLY query clean + title match (alg file)
# STRICT: multi-word title → query me saare significant words hone chahiye
# "FURY" alone ↛ "Fury In The Shadows"
# "FURY IN THE SHADOWS" / "fury shadows" → match
# Single-word title (RRR, Pushpa if only one word) → that word enough
# Year-only → never match
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
    """Noise hatao; years rakh o (name ke saath)."""
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
    PURA significant name match:
    - Title ke saare significant words query me hone chahiye
      (noise/year hata ke: "Fury In The Shadows" → fury, shadows)
    - Sirf pehla word (FURY) → NO match jab title multi-word ho
    - Single-word title (RRR) → us word se match
    - Sirf year → NO match
    - Query me year ho to movie year match (jab available)
    """
    q = clean_search_query(query)
    if not q:
        return False
    q_tokens = [t for t in q.split() if t]
    if not q_tokens:
        return False

    q_years = {t for t in q_tokens if t.isdigit() and len(t) == 4}
    q_words = [t for t in q_tokens if t not in q_years]

    # Year-only → reject
    if not q_words:
        return False

    title_toks = _title_tokens(movie_title or "")
    title_words = [t for t in title_toks if not (t.isdigit() and len(t) == 4)]
    if not title_words:
        return False

    q_set = set(q_words)

    # Year constraint
    if q_years and movie_year is not None:
        try:
            if str(int(movie_year)) not in q_years:
                return False
        except Exception:
            if str(movie_year) not in q_years:
                return False

    # EVERY significant title word must appear in query
    for tw in title_words:
        if tw not in q_set:
            if len(tw) >= 5 and any(tw in qw or qw in tw for qw in q_words if len(qw) >= 4):
                continue
            return False

    return True


def filter_strict_matches(query: str, movies: list) -> list:
    out = []
    for m in movies or []:
        title = m.get("title") or ""
        year = m.get("year")
        if is_strict_title_match(query, title, year):
            out.append(m)
    return out
