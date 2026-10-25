# joins gf_w1.txt + gf_w2.txt + gf_w3.txt (PLAIN group filter — no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = (
    (_dir / "gf_w1.txt").read_text(encoding="utf-8")
    + (_dir / "gf_w2.txt").read_text(encoding="utf-8")
    + (_dir / "gf_w3.txt").read_text(encoding="utf-8")
)
exec(compile(_src, __file__, "exec"), globals())
