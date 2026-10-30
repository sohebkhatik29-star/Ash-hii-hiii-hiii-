# ==============================================================================
# ASH AUTOMATIC MOVIE FILE STORE BOT
# Module: handlers/movie_card.py
# Purpose: Final Movie Cards, quality buttons, clean title on poster
# ==============================================================================

import re
import requests
import string
import random
from typing import Dict, Any, Tuple
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from configs import Config

def shorten_url_if_enabled(original_url: str) -> str:
    if not Config.SHORTLINK_URL or not Config.SHORTLINK_API:
        return original_url
    try:
        alias = "".join(random.choice(string.ascii_letters + string.digits) for _ in range(7))
        api_url = f"https://{Config.SHORTLINK_URL}/api?api={Config.SHORTLINK_API}&url={original_url}&alias={alias}"
        resp = requests.get(api_url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success" or data.get("shortenedUrl"):
                return data.get("shortenedUrl", original_url)
    except Exception as e:
        print(f"[Shortener] Fallback to original URL: {e}")
    return original_url

def quality_sort_key(q_name: str):
    """Sorts qualities low → high."""
    order = {"240p": 0, "360p": 1, "480p": 2, "540p": 3, "SD": 4, "720p": 5, "HD": 6, "1080p": 7, "1440p": 8, "2160p": 9}
    res_rank = 99
    for r, rank in order.items():
        if r in q_name:
            res_rank = rank
            break
    clean_ep = q_name.replace("480p", "").replace("720p", "").replace("1080p", "").replace("2160p", "")
    num_match = re.search(r"\d+", clean_ep)
    ep_num = int(num_match.group(0)) if num_match else 0
    return (res_rank, ep_num)

def make_quality_slug(q_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", q_name).strip("_")
    slug = re.sub(r"_+", "_", slug)
    return slug

def build_final_movie_card_text(movie: Dict[str, Any]) -> str:
    """
    Clean professional card text.
    Title = clean name only + (YEAR). Quality/codec NOT in title.
    """
    title = movie.get("title", "Untitled") or "Untitled"
    # defensive: strip leftover tech tokens + year so card stays clean
    _year = movie.get("year")
    _t = re.sub(
        r"(?i)\b(2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|hevc|x264|x265|webrip|web-dl|bluray|uncut|dual\s*audio|"
        r"hindi|english|esub|10bit|8bit|mkv|mp4)\b",
        " ",
        str(title),
    )
    _t = re.sub(r"\b(19\d{2}|20\d{2})\b", " ", _t)
    _t = re.sub(r"\s+", " ", _t).strip() or str(title)
    title_upper = _t.upper()
    year = _year
    year_str = f" ({year})" if year else ""
    languages = movie.get("languages", ["Hindi"])
    lang_str = " • ".join(languages) if languages else "Hindi"

    qualities = movie.get("qualities", {}) or {}
    # Primary quality label for display
    q_names = list(qualities.keys()) if qualities else []
    primary_q = q_names[0] if q_names else "HD"

    tag = re.sub(r"[^a-zA-Z0-9]", "", title_upper)
    year_tag = f"{year}" if year else ""

    text = f"🎬 <b>{title_upper}{year_str}</b>\n\n"
    text += f"🌐 <b>Language:</b> {lang_str}\n"
    text += f"🎞 <b>Quality:</b> {primary_q}\n\n"
    text += "🍿 <b>Available Downloads:</b>\n\n"
    text += f"#{tag}{year_tag} #movie"
    return text

def build_final_movie_card_buttons(movie: Dict[str, Any], for_group: bool = False) -> InlineKeyboardMarkup:
    movie_id = movie.get("movie_id", "")
    qualities = movie.get("qualities", {})
    sorted_qualities = sorted(qualities.items(), key=lambda item: quality_sort_key(item[0]))

    buttons = []
    for q_name, q_data in sorted_qualities:
        size_str = q_data.get("file_size_formatted", "") or ""
        if not size_str and q_data.get("file_size"):
            try:
                from handlers.parser import format_size
                size_str = format_size(int(q_data["file_size"]))
            except Exception:
                size_str = ""
        label = f"⬇️ Download {q_name}" + (f" ({size_str})" if size_str else "")
        channel_msg_id = q_data.get("channel_msg_id")
        q_slug = make_quality_slug(q_name)

        if for_group:
            if channel_msg_id:
                redirect_url = f"https://t.me/{Config.BOT_USERNAME}?start=f_{channel_msg_id}"
            else:
                redirect_url = f"https://t.me/{Config.BOT_USERNAME}?start=dl_{movie_id}_{q_slug}"
            buttons.append([InlineKeyboardButton(label, url=redirect_url)])
        else:
            if channel_msg_id:
                callback_data = f"f_{channel_msg_id}"
            else:
                callback_data = f"dl_{movie_id}_{q_slug}"
            buttons.append([InlineKeyboardButton(label, callback_data=callback_data)])

    if for_group:
        # Same as indexing posts — Updates Channel only
        buttons.append([
            InlineKeyboardButton("📢 Updates Channel", url="https://t.me/ash_movie_1782")
        ])
    else:
        channel_url = f"https://t.me/{Config.UPDATES_CHANNEL}" if Config.UPDATES_CHANNEL and not str(Config.UPDATES_CHANNEL).startswith("-") else f"https://t.me/{Config.BOT_USERNAME}"
        buttons.append([
            InlineKeyboardButton("📢 Updates Channel", url=channel_url),
            InlineKeyboardButton("❌ Close", callback_data="close_card")
        ])

    return InlineKeyboardMarkup(buttons)

def generate_movie_links(movie_id: str) -> Tuple[str, str]:
    raw_link = f"https://t.me/{Config.BOT_USERNAME}?start=mov_{movie_id}"
    short_link = shorten_url_if_enabled(raw_link)
    return short_link, raw_link
