from __future__ import annotations

import re
from dataclasses import dataclass

from narrate.common import SCENE


# ---------------------------------------------------------------- chunking

@dataclass
class Chunk:
    text: str
    pause: str = "para"   # gap after this chunk: para | scene


_SENT = re.compile(r"(?<=[.!?…][\"”’)\]])\s+|(?<=[.!?…])\s+")


def split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for sent in _SENT.split(text):
        pieces = [sent]
        if len(sent) > limit:  # one enormous sentence: break on clause, then word
            pieces, buf = [], ""
            for w in re.split(r"(?<=[;,:—])\s+|\s+", sent):
                if buf and len(buf) + len(w) + 1 > limit:
                    pieces.append(buf)
                    buf = w
                else:
                    buf = f"{buf} {w}" if buf else w
            if buf:
                pieces.append(buf)
        for s in pieces:
            if cur and len(cur) + len(s) + 1 > limit:
                out.append(cur)
                cur = s
            else:
                cur = f"{cur} {s}" if cur else s
    if cur:
        out.append(cur)
    return out


def chunk_paras(paras: list, limit: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[str] = []
    size = 0

    def flush(pause="para"):
        nonlocal size
        if cur:
            chunks.append(Chunk("\n\n".join(cur), pause))
            cur.clear()
            size = 0

    for p in paras:
        if p == SCENE:
            if cur:
                flush("scene")
            elif chunks:
                chunks[-1].pause = "scene"
            continue
        for piece in split_long(p, limit):
            if cur and size + len(piece) + 2 > limit:
                flush()
            cur.append(piece)
            size += len(piece) + 2
    flush()
    return chunks


def halve(text: str) -> list[str] | None:
    """Split text near its middle for retrying a refused chunk."""
    if len(text) < 60:
        return None
    mid = len(text) // 2
    cuts = [m.end() for m in _SENT.finditer(text)] or \
           [m.end() for m in re.finditer(r"[;,:—]\s+|\n\n", text)] or \
           [m.end() for m in re.finditer(r"\s+", text)]
    if not cuts:
        return None
    cut = min(cuts, key=lambda c: abs(c - mid))
    a, b = text[:cut].strip(), text[cut:].strip()
    return [a, b] if a and b else None
