# ============================================================================
# handlers/search_query.py  — ONLY query clean + strict title match (alg file)
# - Message se movie name extract (noise hatao)
# - Short partial ("THE GREAT") long title pe match nahi
# ============================================================================
import re
from typing import List, Tuple

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
    Example: 'hello bhai please THE GREAT PUNJAB ROBBERY dedo' → 'great punjab robbery'
    Years (19xx/20xx) keep kiye jaate hain.
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
    Strict match:
    - Query ke zyada tar tokens title me hone chahiye
    - Short query (jaise sirf 'great') long title pe match nahi
    - Year diya ho to year match prefer
    """
    q = clean_search_query(query)
    if not q:
        return False
    q_tokens = [t for t in q.split() if t]
    if not q_tokens:
        return False

    title_toks = _title_tokens(movie_title or "")
    if not title_toks:
        return False

    title_set = set(title_toks)
    title_blob = " ".join(title_toks)

    # Year handle
    q_years = {t for t in q_tokens if t.isdigit() and len(t) == 4}
    q_words = [t for t in q_tokens if t not in q_years]
    if not q_words and not q_years:
        return False

    if q_years and movie_year is not None:
        my = str(movie_year).strip()
        if my and my not in q_years and not any(y == my for y in q_years):
            # year mismatch — soft fail only if words also weak
            pass

    if not q_words:
        # only year searched
        return str(movie_year or "") in q_years

    matched = sum(1 for t in q_words if t in title_set or t in title_blob)
    need = max(1, (len(q_words) * 2 + 2) // 3)  # ~67% of query words
    if matched < need:
        return False

    # Short query vs long title: kam se kam title ke half significant tokens cover
    if len(q_words) < max(2, (len(title_toks) + 1) // 2):
        min_cover = max(2, (len(title_toks) + 1) // 2)
        if matched < min_cover:
            return False

    # Single short token (len<=4) only if title itself short
    if len(q_words) == 1 and len(q_words[0]) <= 4:
        if len(title_toks) > 2:
            return False

    return True


def filter_strict_matches(query: str, movies: list) -> list:
    """Keep only movies that strictly match cleaned query."""
    out = []
    for m in movies or []:
        title = m.get("title") or ""
        year = m.get("year")
        if is_strict_title_match(query, title, year):
            out.append(m)
    return out
