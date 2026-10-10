from __future__ import annotations

import json
import sys



RATE = 24000                      # all chunk audio is 24 kHz mono s16
CHARS_PER_MINUTE = 900            # rough narration pace for estimates
SCENE = "\u2063SCENE\u2063"       # paragraph marker for a scene break


# ---------------------------------------------------------------- output

def emit(event: str, **kw) -> None:
    print(json.dumps({"event": event, **kw}, ensure_ascii=False), flush=True)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Fatal(Exception):
    pass
