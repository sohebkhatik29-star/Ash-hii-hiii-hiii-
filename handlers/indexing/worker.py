from pathlib import Path
_dir = Path(__file__).parent
_full = _dir / "worker_full.py"
_fa = _dir / "worker_full_a.py"
_fb = _dir / "worker_full_b.py"
if _full.exists():
    _src = _full.read_text(encoding="utf-8")
elif _fa.exists() and _fb.exists():
    _src = _fa.read_text(encoding="utf-8") + _fb.read_text(encoding="utf-8")
else:
    _src = "".join((_dir / f"worker_p{i}.py").read_text(encoding="utf-8") for i in range(4))
exec(compile(_src, str(_dir / "worker_plain.py"), "exec"), globals())
