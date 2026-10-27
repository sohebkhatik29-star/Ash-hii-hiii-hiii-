# Admin Panel loader
from pathlib import Path
_dir = Path(__file__).parent
_src = (_dir / "ap_p1.txt").read_text(encoding="utf-8") + (_dir / "ap_p2.txt").read_text(encoding="utf-8")
exec(compile(_src, __file__, "exec"), globals())
