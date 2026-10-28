from pathlib import Path
_dir = Path(__file__).parent
_src = "".join((_dir / f"idx_w{i}.txt").read_text(encoding="utf-8") for i in range(1, 3))
exec(compile(_src, __file__, "exec"), globals())
