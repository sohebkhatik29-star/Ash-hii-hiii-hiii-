# ============================================================================
# handlers/search_query.py  — ONLY query clean + title match (alg file)
# - Message se movie name extract (noise hatao)
# - "PUSHPA" → Pushpa The Rise match
# - Short weak tokens long title pe weak match avoid
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
    Years (19xx/20xx) keep.
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
    - Query ke significant tokens title me hone chahiye
    - Single strong token (len>=5) like PUSHPA → match Pushpa The Rise
    - Very short single token (len<=3) long title pe match nahi
    - Year diya ho to soft prefer
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

    q_years = {t for t in q_tokens if t.isdigit() and len(t) == 4}
    q_words = [t for t in q_tokens if t not in q_years]
    if not q_words and not q_years:
        return False

    if not q_words:
        return str(movie_year or "") in q_years

    matched = sum(1 for t in q_words if t in title_set or t in title_blob)
    # Query coverage: ~60% of query words must hit title
    need = max(1, (len(q_words) * 3 + 4) // 5)
    if matched < need:
        return False

    # Single-token queries:
    # - len >= 5 (PUSHPA, ROBBERY, AVENGERS) → allow even on longer titles
    # - len <= 3 → only if title short
    if len(q_words) == 1:
        tok = q_words[0]
        if len(tok) <= 3 and len(title_toks) > 2:
            return False
        # medium token (4 chars): allow if it is first title word or title is short
        if len(tok) == 4 and len(title_toks) > 3:
            if title_toks[0] != tok:
                return False

    return True


def filter_strict_matches(query: str, movies: list) -> list:
    """Keep only movies that match cleaned query."""
    out = []
    for m in movies or []:
        title = m.get("title") or ""
        year = m.get("year")
        if is_strict_title_match(query, title, year):
            out.append(m)
    return out
