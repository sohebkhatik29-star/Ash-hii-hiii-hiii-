# single-file compressed loader
import zlib, base64
from pathlib import Path
_b64 = (Path(__file__).parent / "database_src.b64").read_text(encoding="utf-8").strip()
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
