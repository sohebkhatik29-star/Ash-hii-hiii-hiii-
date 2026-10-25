# compressed loader — needs force_sub_handler_src_1.b64 + _src_2.b64
import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b64 = (_dir / "force_sub_handler_src_1.b64").read_text(encoding="utf-8").strip()
_b64 += (_dir / "force_sub_handler_src_2.b64").read_text(encoding="utf-8").strip()
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
