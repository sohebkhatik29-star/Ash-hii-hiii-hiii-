# joins fs_w1.txt + fs_w2.txt + fs_w3.txt (PLAIN force-sub — no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = (
    (_dir / "fs_w1.txt").read_text(encoding="utf-8")
    + (_dir / "fs_w2.txt").read_text(encoding="utf-8")
    + (_dir / "fs_w3.txt").read_text(encoding="utf-8")
)
exec(compile(_src, __file__, "exec"), globals())
