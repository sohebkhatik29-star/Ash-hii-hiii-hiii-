# Temporary runtime restore of database from last good commit
import importlib.util, urllib.request, tempfile, sys, os

_URL = "https://raw.githubusercontent.com/sohebkhatik29-star/Ash-hii-hiii-hiii-/7aebbab5eaeda8fefc2a9ae4158b50592d2d8369/handlers/database.py"
_path = os.path.join(tempfile.gettempdir(), "ash_database_restored.py")

def _load():
    with urllib.request.urlopen(_URL, timeout=20) as r:
        code = r.read()
    text = code.decode("utf-8")
    if "import re" not in text:
        text = text.replace("import asyncio\n", "import asyncio\nimport re\n")
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
