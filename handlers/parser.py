# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/parser.py
# Purpose: Clean title (poster), distinct quality keys, metadata extract
# ==============================================================================

import re
import difflib
from typing import Dict, Any


def sanitize_text(s) -> str:
    """Strip UTF-16 surrogates and other non-UTF8-safe chars (common in TG filenames)."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return "".join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))


# Distinct quality keys — never collapse 360 into 480 etc.
QUALITY_PATTERNS = [
    (r"(?i)\b(2160p|4k|uhd)\b", "2160p"),
    (r"(?i)\b(1440p|2k)\b", "1440p"),
    (r"(?i)\b(1080p|fhd)\b", "1080p"),
    (r"(?i)\b(720p)\b", "720p"),
    (r"(?i)\b(540p)\b", "540p"),
    (r"(?i)\b(480p)\b", "480p"),
    (r"(?i)\b(360p)\b", "360p"),
    (r"(?i)\b(240p)\b", "240p"),
    (r"(?i)\bhd\b", "HD"),
    (r"(?i)\bsd\b", "SD"),
]

LANGUAGE_PATTERNS = [
    (r"(?i)\b(hindi|hin)\b", "Hindi"),
    (r"(?i)\b(english|eng)\b", "English"),
    (r"(?i)\b(tamil|tam)\b", "Tamil"),
    (r"(?i)\b(telugu|tel)\b", "Telugu"),
    (r"(?i)\b(malayalam|mal)\b", "Malayalam"),
    (r"(?i)\b(kannada|kan)\b", "Kannada"),
    (r"(?i)\b(bengali|ben)\b", "Bengali"),
    (r"(?i)\b(marathi|mar)\b", "Marathi"),
    (r"(?i)\b(punjabi|pun)\b", "Punjabi"),
    (r"(?i)\b(italian|ita)\b", "Italian"),
    (r"(?i)\b(spanish|spa|esp)\b", "Spanish"),
    (r"(?i)\b(french|fre|fra)\b", "French"),
    (r"(?i)\b(dual[\s_-]?audio)\b", "Dual Audio"),
    (r"(?i)\b(multi[\s_-]?audio)\b", "Multi Audio"),
]

TECH_TAGS = r"(?i)\b(2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|2k|uhd|fhd|hd|sd|hevc|x264|x265|h264|h265|avc|10bit|8bit|aac|ac3|dts|ddp5\.1|ddp|eac3|5\.1|5\s*1|esub|esubs|sub|subtitles|bluray|blu-ray|web-dl|webdl|web\s*rip|webrip|hdrip|dvdrip|hdtv|camrip|predvd|hdcam|hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi|dual\s*audio|multi\s*audio|org|original|uncut|remastered|clean|mkv|mp4|avi|webm|webr)\b"


def format_size(size_bytes: int) -> str:
    if not size_bytes or size_bytes <= 0:
        return "Unknown Size"
    power = 1024
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size_bytes >= power and n < len(units) - 1:
        size_bytes /= power
        n += 1
    return f"{size_bytes:.2f} {units[n]}"


def normalize_title(title: str) -> str:
    t = sanitize_text(title or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extract_episode_or_part(text: str) -> str:
    """
    Episode / range extract.
    Supports: S01E01-08, S01 E01-08, E01-08, Ep 1-5, Episode 3, Part 2
    Returns compact label like "E01-08" or "E05" or "Part 2" (empty if none).
    """
    if not text:
        return ""
    # S01E01-16 / S01E05 / S01 E01-08
    m = re.search(
        r"(?i)\bS\s*\d{1,2}\s*E\s*(\d{1,3})(?:\s*[-\u2013to]+\s*E?\s*(\d{1,3}))?\b",
        text,
    )
    if m:
        a = int(m.group(1))
        if m.group(2):
            return f"E{a:02d}-{int(m.group(2)):02d}"
        return f"E{a:02d}"
    # Standalone E01-08 / E01 (no leading S)
    m = re.search(
        r"(?i)\bE(?:P|PS)?\s*(\d{1,3})(?:\s*[-\u2013to]+\s*E?(?:P|PS)?\s*(\d{1,3}))?\b",
        text,
    )
    if m:
        a = int(m.group(1))
        if m.group(2):
            return f"E{a:02d}-{int(m.group(2)):02d}"
        return f"E{a:02d}"
    # "Ep 1-5" / "Episode 3" / "episodes 01-08"
    ep_match = re.search(
        r"(?i)\[?\b(?:ep|eps|episode|episodes)\s*0*(\d+\s*(?:[-\u2013to]+\s*0*\d+)?)\]?",
        text,
    )
    if ep_match:
        raw_ep = ep_match.group(1).strip()
        raw_ep = re.sub(r"\s*[-\u2013to]+\s*", "-", raw_ep)
        parts = raw_ep.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"E{int(parts[0]):02d}-{int(parts[1]):02d}"
        if len(parts) == 1 and parts[0].isdigit():
            return f"E{int(parts[0]):02d}"
        return f"E{raw_ep}"
    part_match = re.search(r"(?i)\bpart\s*0*(\d+)\b", text)
    if part_match:
        return f"Part {int(part_match.group(1))}"
    return ""


def extract_season(text: str) -> str:
    if not text:
        return ""
    # S02E01 / S02 E01 form (no word-boundary after season digits)
    m = re.search(r"(?i)\bS\s*0*(\d{1,2})\s*E\s*\d", text)
    if m:
        return f"S{int(m.group(1)):02d}"
    s_match = re.search(r"(?i)\b(?:s|season)\s*0*(\d{1,2})\b", text)
    if s_match:
        return f"S{int(s_match.group(1)):02d}"
    return ""


def clean_movie_title(raw_name: str, year: int = None) -> str:
    """
    Clean title for poster/card.
    Example: "Pushpa The Rise 2021 WebRip 720p x265 HEVC Hindi ESub.mkv"
          -> "Pushpa The Rise"
    Year is NOT kept inside title (card adds (YEAR) separately).
    """
    cleaned = sanitize_text(raw_name or "")
    if not cleaned:
        return "Untitled Movie"

    cleaned = re.sub(r"@[\w_]+", " ", cleaned)
    cleaned = re.sub(r"(?i)\b([a-z])\.([a-z])\.([a-z])\.?\b", r"\1\2\3", cleaned)
    cleaned = re.sub(r"(?i)\b([a-z])\.([a-z])\.?\b", r"\1\2", cleaned)

    if year:
        year_match = re.search(r"\b" + str(year) + r"\b", cleaned)
        if year_match:
            before = cleaned[: year_match.start()]
            before = re.sub(r"[\(\[\{\-_.\s]+$", "", before).strip()
            if len(before) >= 2:
                cleaned = before

    def _bracket(m):
        content = (m.group(1) or "").strip()
        if not content:
            return " "
        if re.search(TECH_TAGS, content) or content.startswith("@") or "channel" in content.lower():
            return " "
        if re.fullmatch(r"(19\d{2}|20\d{2})", content):
            return " "
        return f" {content} "

    cleaned = re.sub(r"[\[\(\{](.*?)[\]\)\}]", _bracket, cleaned)

    JUNK = [
        r"(?i)\b(2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|2k|uhd|fhd)\b",
        r"(?i)\b(hevc|x264|x265|h264|h265|avc|10bit|8bit|aac|ac3|dts|ddp5\.1|ddp|eac3)\b",
        r"(?i)\b(5\.1|5\s*1|2\.0|atmos)\b",
        r"(?i)\b(esub|esubs|sub|subs|subtitles)\b",
        r"(?i)\b(bluray|blu-ray|web-dl|webdl|web\s*rip|webrip|hdrip|dvdrip|hdtv|camrip|predvd|hdcam|webr)\b",
        r"(?i)\b(uncut|extended|remastered|unrated|clean|org|original)\b",
        r"(?i)\b(dual\s*audio|multi\s*audio|hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|punjabi)\b",
        r"(?i)\b(mkv|mp4|avi|webm|m4v)\b",
        r"(?i)\b(?:ep|eps|episode|episodes)\s*0*\d+(?:\s*[-\u2013to]+\s*0*\d+)?\b",
        r"(?i)\bS\s*\d{1,2}\s*E\s*\d{1,3}(?:\s*[-\u2013to]+\s*E?\s*\d{1,3})?\b",
        r"(?i)\bS\s*\d{1,2}\b",
        r"(?i)\bE\s*\d{1,3}(?:\s*[-\u2013to]+\s*E?\s*\d{1,3})?\b",
        r"(?i)\b(yify|rarbg|psa|codex|sparks|hdtv)\b",
        # site / pack spam often left in title after year strip
        r"(?i)\b(filmyz4u|filmywap|moviesverse|mkvcinemas|9xmovies|bollyflix|mkvcinema|skymovies|hdhub4u)\b",
        r"(?i)\b(www|http|https|amzn|nf|netflix|prime|hotstar|disney|sonyliv)\b",
        r"(?i)\b(complete|pack|web\s*series|webseries|series|season)\b",
        r"(?i)\b(new|final|release|date|line|print)\b",
        r"\b(19\d{2}|20\d{2})\b",
        r"[:._\-+|/\\]+",
    ]
    for p in JUNK:
        cleaned = re.sub(p, " ", cleaned)

    cleaned = re.sub(r"[\(\)\[\]\{\}]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned or len(cleaned) < 2:
        raw = sanitize_text(raw_name or "")
        raw = re.sub(r"@[\w_]+", " ", raw)
        m = re.search(
            r"(?i)\b(2160p|1440p|1080p|720p|480p|360p|4k|uhd|hevc|x264|x265)\b",
            raw,
        )
        if m:
            cleaned = raw[: m.start()].strip(" .-_()[]{}")
        else:
            cleaned = re.sub(r"\.[a-z0-9]{2,4}$", "", raw, flags=re.I).strip()

    # Title case words, keep short ALLCAPS acronyms
    words = []
    for w in cleaned.split():
        if len(w) <= 3 and w.isupper():
            words.append(w)
        else:
            words.append(w.capitalize() if not w.isupper() else w)
    cleaned = " ".join(words)

    # Fix common all-lower remnants
    if cleaned.islower():
        cleaned = cleaned.title()

    return cleaned or "Untitled Movie"


def extract_movie_metadata(filename: str, caption: str = "") -> Dict[str, Any]:
    filename = sanitize_text(filename or "")
    caption = sanitize_text(caption or "")
    # Combine both so quality/year/lang tags in caption are never missed
    combined = f"{filename} {caption}".strip()

    quality = "HD"
    for pattern, q in QUALITY_PATTERNS:
        if re.search(pattern, combined):
            quality = q
            break

    year = None
    ym = re.search(r"\b(19\d{2}|20\d{2})\b", combined)
    if ym:
        try:
            year = int(ym.group(1))
        except Exception:
            year = None

    language = "Unknown"
    for pattern, lang in LANGUAGE_PATTERNS:
        if re.search(pattern, combined):
            language = lang
            break

    season = extract_season(combined)
    episode = extract_episode_or_part(combined)

    # Prefer cleaner title: spam-filled long filenames (PNCHYT S2 NEW Www Filmyz4u...)
    # used to win on length and pollute the session title. Score by cleaned length
    # (short clean name is better) + slight preference for caption.
    def _base(s: str) -> str:
        s = re.sub(r"\.[a-z0-9]{2,4}$", "", s or "", flags=re.I).strip()
        return s
    fn_base = _base(filename)
    cap_base = _base(caption)
    generic = {"", "video", "document", "movie", "file", "media", "unknown"}
    candidates = []
    if fn_base and fn_base.lower() not in generic:
        candidates.append(("fn", fn_base))
    if cap_base and cap_base.lower() not in generic:
        candidates.append(("cap", cap_base))
    if not candidates:
        candidates = [("raw", fn_base or cap_base or "")]

    def _score(item):
        src, raw = item
        cleaned = clean_movie_title(raw, year)
        cl = (cleaned or "").strip()
        if not cl or cl.lower() == "untitled movie":
            return (0, 0, 0)
        # Prefer shorter cleaned titles (less leftover spam), caption slight boost
        spam_penalty = 0
        low = raw.lower()
        for junk in ("filmyz", "www", "http", "amzn", "complete", "org", "new www"):
            if junk in low:
                spam_penalty += 1
        # higher is better
        return (1, -spam_penalty, -len(cl), 1 if src == "cap" else 0, cleaned)

    ranked = sorted((_score(c) for c in candidates), reverse=True)
    title = "Untitled Movie"
    for sc in ranked:
        if sc[0] == 1 and sc[-1]:
            title = sc[-1]
            break
    if not title or not title.strip():
        title = "Untitled Movie"

    # Series: keep season in title so S01 / S02 of same year stay separate movies
    # (e.g. Vikings S01 vs Vikings S02). Same-season qualities still merge under one post.
    if season and season.upper() not in title.upper():
        title = f"{title} {season}".strip()
    # Episode: same name + year + season + different EP → separate movie + separate filter
    # (e.g. Vikings S02 E01 vs Vikings S02 E02). Same-EP qualities still merge under one post.
    if episode and episode.upper() not in title.upper():
        title = f"{title} {episode}".strip()

    return {
        "title": title,
        "normalized_title": normalize_title(title),
        "year": year,
        "quality": quality,
        "language": language,
        "season": season,
        "episode": episode,
        "raw_filename": filename,
    }


def is_title_match(active_title: str, incoming_title: str, threshold: float = 0.72) -> bool:
    """Same movie/series session check.
    Handles messy vs clean titles: 'PNCHYT S2 ...' vs 'Panchayat S02'.
    Season mismatch → no match. Same season → compare core name tokens.
    """
    a = normalize_title(active_title)
    b = normalize_title(incoming_title)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True

    # Season must agree when both present (S01 vs S02 = different show entry)
    sa = extract_season(active_title or "")
    sb = extract_season(incoming_title or "")
    if sa and sb and sa != sb:
        return False

    # Strip season/episode noise for core comparison
    def _core(t: str) -> str:
        t = re.sub(r"(?i)\bS\s*\d{1,2}\b", " ", t)
        t = re.sub(r"(?i)\bE\s*\d{1,3}(?:\s*-\s*\d{1,3})?\b", " ", t)
        t = re.sub(r"(?i)\b(complete|pack|web\s*series|series|season|new|www|org|amzn|nf)\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return difflib.SequenceMatcher(None, a, b).ratio() >= threshold
    if ca == cb or ca in cb or cb in ca:
        return True

    ratio = difflib.SequenceMatcher(None, ca, cb).ratio()
    if ratio >= threshold:
        return True

    # Token overlap: at least one significant token (>=4 chars) shared
    # covers PNCHYT vs Panchayat poorly, so also try prefix / fuzzy token
    ta = {w for w in ca.split() if len(w) >= 3}
    tb = {w for w in cb.split() if len(w) >= 3}
    if ta and tb:
        inter = ta & tb
        if inter:
            return True
        # fuzzy: any token shares long common prefix (panchayat / pnchyt-ish)
        for x in ta:
            for y in tb:
                if len(x) >= 4 and len(y) >= 4:
                    if x[:4] == y[:4] or difflib.SequenceMatcher(None, x, y).ratio() >= 0.7:
                        return True
    return ratio >= max(0.55, threshold - 0.15)


def disambiguate_title_by_language(title: str, language: str = None) -> str:
    """Same name+year different language → unique title.
    Boss + Bengali → Boss (Bengali). Hindi/Unknown stay as-is.
    """
    t = (title or "").strip()
    if not t:
        return t
    lang = (language or "").strip()
    if not lang or lang.lower() in ("unknown", "unk", "hindi", "hin"):
        return t
    # already has language tag?
    low = t.lower()
    if lang.lower() in low or f"({lang.lower()})" in low:
        return t
    return f"{t} ({lang})"

