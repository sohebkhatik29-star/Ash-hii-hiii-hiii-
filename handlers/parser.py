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
    title = sanitize_text(title)
    if not title:
        return ""
    normalized = title.lower()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_episode_or_part(text: str) -> str:
    if not text:
        return ""
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
            r"(?i)\b(2160p|1440p|1080p|720p|480p|360p|4k|hevc|x264|x265|webrip|web-dl|bluray)\b",
            raw,
        )
        chunk = raw[: m.start()] if m else raw
        chunk = re.sub(r"[\(\)\[\]\{\}._\-+]+", " ", chunk)
        chunk = re.sub(r"\s+", " ", chunk).strip()
        cleaned = chunk if len(chunk) >= 2 else "Untitled Movie"

    words = []
    for w in cleaned.split():
        w_up = w.upper()
        if w_up in ("KGF", "RRR", "PK", "DON", "RAW", "CID", "WAR", "DDLJ", "JFK", "AI"):
            words.append(w_up)
        elif len(w) <= 3 and w.isalpha() and w.lower() not in ("the", "and", "for", "in", "of", "to", "a", "an"):
            words.append(w_up)
        else:
            words.append(w.capitalize())

    return " ".join(words) if words else "Untitled Movie"


def extract_movie_metadata(filename: str, caption: str = "") -> Dict[str, Any]:
    filename = sanitize_text(filename or "")
    caption = sanitize_text(caption or "")
    raw_name = filename or caption or "Untitled_Movie"
    base_name = raw_name

    quality = "HD"
    for pattern, q in QUALITY_PATTERNS:
        if re.search(pattern, base_name):
            quality = q
            break

    year = None
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", base_name)
    if year_match:
        year = int(year_match.group(1))

    languages = []
    for pattern, lang in LANGUAGE_PATTERNS:
        if re.search(pattern, base_name):
            if lang not in languages:
                languages.append(lang)

    season = extract_season(base_name)
    episode = extract_episode_or_part(base_name)
    title = clean_movie_title(base_name, year)

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
    norm_active = normalize_title(active_title)
    norm_incoming = normalize_title(incoming_title)
    if not norm_active or not norm_incoming:
        return False
    if norm_active == norm_incoming:
        return True
    a_tokens = set(norm_active.split())
    i_tokens = set(norm_incoming.split())
    if a_tokens and i_tokens:
        overlap_active = len(a_tokens & i_tokens) / len(a_tokens)
        overlap_incoming = len(a_tokens & i_tokens) / len(i_tokens)
        if overlap_active >= 0.75 or overlap_incoming >= 0.75:
            return True
    ratio = difflib.SequenceMatcher(None, norm_active, norm_incoming).ratio()
    return ratio >= threshold
