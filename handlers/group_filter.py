# compressed loader — needs group_filter_src_1..2.b64
import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b64 = "".join((
    (_dir / f"group_filter_src_{i}.b64").read_text(encoding="utf-8").strip()
    for i in range(1, 2+1)
))
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
