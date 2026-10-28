from pathlib import Path
_src = (Path(__file__).parent / "worker_plain.py").read_text(encoding="utf-8")
exec(compile(_src, str(Path(__file__).parent / "worker_plain.py"), "exec"), globals())
