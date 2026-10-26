# FAST addmovie — joins sm_p1.txt + sm_p2.txt + sm_p3.txt (plain, no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = (
    (_dir / "sm_p1.txt").read_text(encoding="utf-8")
    + (_dir / "sm_p2.txt").read_text(encoding="utf-8")
    + (_dir / "sm_p3.txt").read_text(encoding="utf-8")
)
exec(compile(_src, __file__, "exec"), globals())
