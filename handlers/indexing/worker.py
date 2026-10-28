import base64
from pathlib import Path
_dir = Path(__file__).parent
_p = _dir / "worker_src.b64"
if _p.exists():
    _src = base64.b64decode(_p.read_text().strip()).decode("utf-8")
else:
    _b = "".join((_dir / f"n{i:02d}.txt").read_text().strip() for i in range(19))
    _src = base64.b64decode(_b).decode("utf-8")
exec(compile(_src, str(_dir / "worker.py"), "exec"), globals())
