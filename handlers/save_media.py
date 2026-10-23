# Temporary runtime restore of save_media from last good commit
import importlib.util, urllib.request, tempfile, sys, os

_URL = "https://raw.githubusercontent.com/sohebkhatik29-star/Ash-hii-hiii-hiii-/7aebbab5eaeda8fefc2a9ae4158b50592d2d8369/handlers/save_media.py"
_path = os.path.join(tempfile.gettempdir(), "ash_save_media_restored.py")

def _load():
    with urllib.request.urlopen(_URL, timeout=20) as r:
        code = r.read()
    with open(_path, "wb") as f:
        f.write(code)
    spec = importlib.util.spec_from_file_location("handlers._save_media_restored", _path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["handlers._save_media_restored"] = mod
    spec.loader.exec_module(mod)
    return mod

_mod = _load()
handle_incoming_movie_file = _mod.handle_incoming_movie_file
handle_generate_links_action = _mod.handle_generate_links_action
