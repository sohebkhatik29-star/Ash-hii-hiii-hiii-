# handlers/admin_panel.py — plain source parts (no zlib)
from pathlib import Path
_dir = Path(__file__).resolve().parent
_code = "".join((_dir / f"ap_src_{i}.txt").read_text(encoding="utf-8") for i in range(5))
exec(compile(_code, __file__, "exec"), globals())
