# joins sm_w1.txt + sm_w2.txt (PLAIN save_media / addmovie — no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = (
    (_dir / "sm_w1.txt").read_text(encoding="utf-8")
    + (_dir / "sm_w2.txt").read_text(encoding="utf-8")
)
exec(compile(_src, __file__, "exec"), globals())
