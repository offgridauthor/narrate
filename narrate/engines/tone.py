from __future__ import annotations

import json
import math
import struct

from narrate.audio import pcm_to_wav
from narrate.chunking import _SENT
from narrate.common import RATE
from narrate.engines.base import Engine


class ToneEngine(Engine):
    """Dry run: a soft tick per sentence, 10x faster than speech. Checks
    extraction, chapters, and assembly without any speech engine installed."""
    name = "tone"
    max_chars = 1800

    def identity(self):
        return json.dumps(["tone"])

    def synth(self, text):
        n = max(1, len(_SENT.split(text)))
        secs = max(0.3, len(text) / 150)
        tick = b"".join(struct.pack("<h", int(1800 * math.sin(2 * math.pi * 660 * i / RATE)))
                        for i in range(int(RATE * 0.04)))
        per = int(RATE * secs / n) * 2
        pcm = b"".join((tick + b"\x00" * per)[:per] for _ in range(n))
        return pcm_to_wav(pcm)
