import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b = (_dir / "panel_a.b64").read_text().strip() + (_dir / "panel_b.b64").read_text().strip()
_pad = (4 - len(_b) % 4) % 4
_src = zlib.decompress(base64.b64decode(_b + "=" * _pad)).decode("utf-8")
exec(compile(_src, str(_dir / "panel.py"), "exec"), globals())
