# Runtime restore of database.py from last good commit (Render-safe DATA_DIR + year ASC sort)
import importlib.util
import urllib.request
import tempfile
import sys
import os

_URL = (
    "https://raw.githubusercontent.com/sohebkhatik29-star/Ash-hii-hiii-hiii-/"
    "7aebbab5eaeda8fefc2a9ae4158b50592d2d8369/handlers/database.py"
)
_path = os.path.join(tempfile.gettempdir(), "ash_database_restored.py")

def _load():
    with urllib.request.urlopen(_URL, timeout=25) as r:
        text = r.read().decode("utf-8")

    # Ensure regex import (used by search_movies)
    if "import re" not in text:
        text = text.replace("import asyncio\n", "import asyncio\nimport re\n")

    # Fix DATA_DIR so it never tries to create /data on Render
    old = (
        'DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")\n'
        "os.makedirs(DATA_DIR, exist_ok=True)"
    )
    new = (
        "_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n"
        'DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(_BASE, "data")\n'
        "try:\n"
        "    os.makedirs(DATA_DIR, exist_ok=True)\n"
        "except OSError:\n"
        '    DATA_DIR = "/tmp/ash_movie_data"\n'
        "    os.makedirs(DATA_DIR, exist_ok=True)"
    )
    if old in text:
        text = text.replace(old, new)
    else:
        text = text.replace(
            "os.makedirs(DATA_DIR, exist_ok=True)",
            "try:\n    os.makedirs(DATA_DIR, exist_ok=True)\nexcept OSError:\n"
            "    DATA_DIR = '/tmp/ash_movie_data'\n"
            "    os.makedirs(DATA_DIR, exist_ok=True)",
            1,
        )

    # Sort by year ASC (oldest first: Pushpa 2021 above Pushpa 2 2025)
    old_sort = 'results.sort(key=lambda x: (x.get("year") or 0, x.get("title", "")), reverse=True)'
    new_sort = 'results.sort(key=lambda x: (x.get("year") or 9999, x.get("title", "") or ""))'
    if old_sort in text:
        text = text.replace(old_sort, new_sort)

    with open(_path, "w", encoding="utf-8") as f:
        f.write(text)

    spec = importlib.util.spec_from_file_location("handlers._database_restored", _path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["handlers._database_restored"] = mod
    spec.loader.exec_module(mod)
    return mod

_mod = _load()
db = _mod.db
Database = _mod.Database
