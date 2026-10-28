from pathlib import Path
_dir = Path(__file__).parent
try:
    _src = "".join((_dir / f"worker_p{i}.py").read_text(encoding="utf-8") for i in range(4))
except Exception:
    _src = (_dir / "worker_full.py").read_text(encoding="utf-8")
exec(compile(_src, str(_dir / "worker_plain.py"), "exec"), globals())
