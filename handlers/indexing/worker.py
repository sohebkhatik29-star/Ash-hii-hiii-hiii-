import base64
from pathlib import Path
_dir = Path(__file__).parent
_p = _dir / "worker_src.b64"
_pa = _dir / "worker_src_a.b64"
_pb = _dir / "worker_src_b.b64"
if _p.exists():
    _b = _p.read_text().strip()
elif _pa.exists() and _pb.exists():
    _b = _pa.read_text().strip() + _pb.read_text().strip()
else:
    _b = "".join((_dir / f"n{i:02d}.txt").read_text().strip() for i in range(19))
_src = base64.b64decode(_b).decode("utf-8")
exec(compile(_src, str(_dir / "worker.py"), "exec"), globals())
