# joins db_w1.txt + db_w2.txt (PLAIN full MovieDatabase — no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = (_dir / "db_w1.txt").read_text(encoding="utf-8") + (_dir / "db_w2.txt").read_text(encoding="utf-8")
exec(compile(_src, __file__, "exec"), globals())
