# Full database loader — joins database_src_part_1..3.txt (plain source, no b64/zlib)
from pathlib import Path
_dir = Path(__file__).parent
_src = "".join((_dir / f"database_src_part_{i}.txt").read_text(encoding="utf-8") for i in range(1, 4))
exec(compile(_src, __file__, "exec"), globals())
