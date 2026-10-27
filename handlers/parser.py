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

    # Prefer filename for title base; caption only if filename empty/generic
    base = filename if filename else caption
    # strip extension
    base = re.sub(r"\.[a-z0-9]{2,4}$", "", base, flags=re.I)
    title = clean_movie_title(base, year)

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
    a = normalize_title(active_title)
    b = normalize_title(incoming_title)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    return ratio >= threshold
