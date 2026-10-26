# ORIGINAL force-sub recovered — joins fs_q1..fs_q4 (plain, no zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = "".join(
    (_dir / f"fs_q{i}.txt").read_text(encoding="utf-8") for i in range(1, 5)
)
exec(compile(_src, __file__, "exec"), globals())
