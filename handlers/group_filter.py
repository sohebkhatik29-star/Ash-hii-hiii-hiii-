# compressed loader — needs group_filter_src_1.b64 .. _2.b64
import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b64 = ""
for _i in range(1, 2+1):
    _b64 += (_dir / f"group_filter_src_{_i}.b64").read_text(encoding="utf-8").strip()
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
