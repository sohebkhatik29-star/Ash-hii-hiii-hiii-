# -*- coding: utf-8 -*-
# Self-contained force_sub_handler — no external .b64 needed
import zlib, base64
_SRC_B64 = """REPLACE_ME"""
_src = zlib.decompress(base64.b64decode(_SRC_B64)).decode("utf-8")
exec(compile(_src, __file__, "exec"), globals())
