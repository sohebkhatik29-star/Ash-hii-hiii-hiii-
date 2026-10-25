# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT 🎬
# Module: handlers/parser.py
# Purpose: Smart movie & web-series title normalization, acronym handling (KGF, RRR),
#          bracket unboxing, season/episode detection, and quality key generation.
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
    # Drop lone surrogates that break utf-8 encode
    return "".join(ch for ch in s if not (0xD800 <= ord(ch) <= 0xDFFF))


# Quality mapping with regex patterns
QUALITY_PATTERNS = [
    (r"(?i)\b(2160p|4k|uhd)\b", "2160p"),
    (r"(?i)\b(1080p|fhd|1080)\b", "1080p"),
    (r"(?i)\b(720p|hd|720)\b", "720p"),
    (r"(?i)\b(480p|sd|480)\b", "480p"),
    (r"(?i)\b(540p|360p)\b", "480p"),
]

# Common audio & language patterns
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

# Known technical tags to strip from titles
TECH_TAGS = r"(?i)\b(720p|1080p|2160p|480p|540p|360p|4k|uhd|fhd|hd|hevc|x264|x265|h264|h265|avc|10bit|8bit|aac|ac3|dts|ddp5\.1|eac3|5\.1|esub|esubs|sub|subtitles|bluray|blu-ray|web-dl|webdl|webrip|hdrip|dvdrip|hdtv|camrip|predvd|hdcam|hindi|english|tamil|telugu|malayalam|kannada|marathi|bengali|dual\s*audio|multi\s*audio|org|original|uncut|remastered|clean|mkv|mp4|avi|webm)\b"


def format_size(size_bytes: int) -> str:
    """Format bytes to human readable string (e.g. 450 MB, 1.25 GB)."""
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
    """Normalize a movie title for strict session matching."""
    title = sanitize_text(title)
    if not title:
        return ""
    normalized = title.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_episode_or_part(text: str) -> str:
    """
    Extracts episode range or part (e.g. 'Ep 01-05', 'Ep 06-10', 'Part 1', 'Ep 1').
    """
    if not text:
        return ""

    # 1. Ep 01 - 05 or Episode 01 to 05
    ep_match = re.search(r"(?i)\[?\b(?:ep|eps|episode|episodes)\s*0*(\d+\s*(?:[-\u2013to]+\s*0*\d+)?)\]?", text)
    if ep_match:
        raw_ep = ep_match.group(1).strip()
        raw_ep = re.sub(r"\s*[-\u2013to]+\s*", "-", raw_ep)
        parts = raw_ep.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"Ep {int(parts[0]):02d}-{int(parts[1]):02d}"
        elif len(parts) == 1 and parts[0].isdigit():
            return f"Ep {int(parts[0]):02d}"
        return f"Ep {raw_ep}"

    # 2. Part 1 / Part 2
    part_match = re.search(r"(?i)\bpart\s*0*(\d+)\b", text)
    if part_match:
        return f"Part {int(part_match.group(1))}"

    return ""


def extract_season(text: str) -> str:
    if not text:
        return ""
    s_match = re.search(r"(?i)\b(?:s|season)\s*0*(\d{1,2})\b", text)
    if s_match:
        return f"S{int(s_match.group(1)):02d}"
    return ""


def clean_movie_title(raw_name: str, year: int = None) -> str:
    """
    Cleans movie title with special handling for acronyms (KGF, RRR, PK)
    and bracket unboxing so [KGF] Chapter 1 is never lost.
    """
    cleaned = sanitize_text(raw_name)

    # 1. Strip telegram usernames e.g. @Movies_1782
    cleaned = re.sub(r"@[\w_]+", " ", cleaned)

    # 2. If year exists, clip before year
    if year:
        year_match = re.search(r"\b" + str(year) + r"\b", cleaned)
        if year_match:
            up_to_year = cleaned[:year_match.start()]
            up_to_year = re.sub(r"[\(\[\{\-_.\s]+$", "", up_to_year)
            if len(up_to_year.strip()) >= 2:
                cleaned = up_to_year

    # 3. Normalize dotted acronyms: K.G.F. -> KGF, R.R.R. -> RRR
    cleaned = re.sub(r"(?i)\b([a-z])\.([a-z])\.([a-z])\.?\b", r"\1\2\3", cleaned)
    cleaned = re.sub(r"(?i)\b([a-z])\.([a-z])\.?\b", r"\1\2", cleaned)

    # 4. Smart bracket unboxing
    def bracket_unwrapper(m):
        content = m.group(1).strip()
        if re.search(TECH_TAGS, content) or content.startswith("@") or "channel" in content.lower():
            return " "
        return f" {content} "

    cleaned = re.sub(r"\[(.*?)\]", bracket_unwrapper, cleaned)

    # 5. Remove junk words
    JUNK = [
        r"\(.*?\b(yify|rarbg|psa|bolly|vj|codex|ash|tele|channel|unrated|extended|remastered|web series)\b.*?\)",
        r"(?i)\b(web\s*series|series|unrated|bluray|blu-ray|web-dl|webdl|webrip|hdrip|dvdrip|hdtv|camrip|predvd|hdcam)\b",
        r"(?i)\b(x264|x265|hevc|h264|h265|avc|10bit|8bit|aac|ac3|dts|ddp5\.1|eac3|5\.1)\b",
        r"(?i)\b(esub|esubs|sub|subtitles|clean|org|original|uncut|extended|remastered)\b",
        r"(?i)\b(hindi|english|telugu|tamil|malayalam|kannada|marathi|bengali|dual\s*audio|multi\s*audio)\b",
        r"(?i)\b(mkv|mp4|avi|webm)\b",
        r"(?i)\b(?:ep|eps|episode|episodes)\s*0*(\d+\s*(?:[-\u2013to]+\s*0*\d+)?)\b",
        r"(?i)\b(2160p|1080p|720p|480p|540p|360p|4k)\b",
        r"\b(19\d{2}|20\d{2})\b",
        r"[:._\-+]+",
    ]
    for p in JUNK:
        cleaned = re.sub(p, " ", cleaned)

    cleaned = re.sub(r"[\(\)\[\]\{\}]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 6. Format words with proper casing
    words = []
    for w in cleaned.split():
        w_up = w.upper()
        if w_up in ("KGF", "RRR", "PK", "DON", "RAW", "CID", "WAR", "DDLJ"):
            words.append(w_up)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for", "in", "of", "to"):
            words.append(w_up)
        else:
            words.append(w.capitalize())

    final_title = " ".join(words) if words else "Untitled Movie"
    return final_title


def extract_movie_metadata(filename: str, caption: str = "") -> Dict[str, Any]:
    """
    Extracts movie title, season, episode, release year, quality, and languages.
    """
    filename = sanitize_text(filename or "")
    caption = sanitize_text(caption or "")
    raw_name = filename or caption or "Untitled_Movie"
    base_name = raw_name

    # 1. Extract Quality
    quality = "HD"
    for pattern, q in QUALITY_PATTERNS:
        if re.search(pattern, base_name):
            quality = q
            break

    # 2. Extract Year
    year = None
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", base_name)
    if year_match:
        year = int(year_match.group(1))

    # 3. Extract languages
    languages = []
    for pattern, lang in LANGUAGE_PATTERNS:
        if re.search(pattern, base_name):
            if lang not in languages:
                languages.append(lang)

    # 4. Season / Episode
    season = extract_season(base_name)
    episode = extract_episode_or_part(base_name)

    # 5. Clean title
    title = clean_movie_title(base_name, year)

    # Prefer caption if filename is generic
    if caption and (not filename or filename.lower() in ("video", "video.mp4", "video.mkv")):
        caption_meta_title = clean_movie_title(caption, year)
        if caption_meta_title and caption_meta_title != "Untitled Movie":
            title = caption_meta_title

    normalized_title = normalize_title(title)

    return {
        "title": title,
        "normalized_title": normalized_title,
        "year": year,
        "quality": quality,
        "languages": languages or ["Hindi"],
        "season": season,
        "episode": episode,
        "raw_name": raw_name,
    }


def is_title_match(active_title: str, incoming_title: str, threshold: float = 0.72) -> bool:
    """
    Fuzzy match between current session title and incoming file title.
    Prevents mixing different movies in one session.
    """
    norm_active = normalize_title(active_title)
    norm_incoming = normalize_title(incoming_title)
    if not norm_active or not norm_incoming:
        return False
    if norm_active == norm_incoming:
        return True

    # Token overlap
    a_tokens = set(norm_active.split())
    i_tokens = set(norm_incoming.split())
    if a_tokens and i_tokens:
        overlap_active = len(a_tokens & i_tokens) / len(a_tokens)
        overlap_incoming = len(a_tokens & i_tokens) / len(i_tokens)
        if overlap_active >= 0.75 or overlap_incoming >= 0.75:
            return True

    ratio = difflib.SequenceMatcher(None, norm_active, norm_incoming).ratio()
    return ratio >= threshold
