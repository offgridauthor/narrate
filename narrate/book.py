from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from narrate.common import Fatal, SCENE


# ---------------------------------------------------------------- book model

@dataclass
class Chapter:
    title: str
    paras: list = field(default_factory=list)

    def chars(self) -> int:
        return sum(len(p) for p in self.paras if p != SCENE)


@dataclass
class Book:
    title: str
    author: str = ""
    chapters: list = field(default_factory=list)
    cover: bytes | None = None
    cover_ext: str = "jpg"


_ZW = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"), None)
_URL = re.compile(r"(?:https?://|www\.)\S+")
_SEPARATOR = re.compile(r"^[\s*#~•·⁂◆◇❖§_=\-–—]{1,20}$")


def clean(s: str) -> str:
    s = unicodedata.normalize("NFC", s).translate(_ZW)
    s = _URL.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def is_separator(s: str) -> bool:
    """A line like '* * *', '###', or '⁂' that marks a scene break."""
    s = s.strip()
    return bool(s) and bool(_SEPARATOR.match(s)) and not s.isalnum() \
        and (len(s.replace(" ", "")) >= 3 or s in "⁂◆❖§")


def finalize(book: Book) -> Book:
    chapters = []
    for ch in book.chapters:
        out = []
        for p in ch.paras:
            if p == SCENE or is_separator(p):
                if out and out[-1] != SCENE:
                    out.append(SCENE)
                continue
            p = clean(p)
            if p:
                out.append(p)
        while out and out[-1] == SCENE:
            out.pop()
        if out:
            chapters.append(Chapter(clean(ch.title) or f"Part {len(chapters) + 1}", out))
    if not chapters:
        raise Fatal("no readable text found")
    book.chapters = chapters
    book.title = clean(book.title) or "Untitled"
    book.author = clean(book.author)
    return book


# How chapters fall when a book has parts with headed chapters inside them:
# "parts" makes each inner chapter a chapter of its own, titled with its part
# ("Part One — The Crossing"); "top" keeps one chapter per part. load_book sets
# it from the profiles file's `split` or the command line's --split.
SPLIT: ContextVar[str] = ContextVar("narrate_split", default="parts")
PART_SEP = " \u2014 "


def build_chapters(items: list, fallback_title: str) -> list:
    """items: (level or None, text). Chapters start at the shallowest heading
    level, or one level deeper when that level has a single wrapper heading.
    With SPLIT at "parts", a top-level section that has headings one level
    down is split at them, so a part's chapters each get a chapter mark."""
    levels = sorted({lv for lv, _ in items if lv})
    if not levels:
        return [Chapter(fallback_title, [t for _, t in items])]
    top = levels[0]
    if len(levels) > 1 and sum(1 for lv, _ in items if lv == top) == 1:
        top = levels[1]
    deeper = [lv for lv in levels if lv > top]
    sub = deeper[0] if deeper and SPLIT.get() == "parts" else None
    # which top-level sections contain a heading at `sub`
    split_sections, section = set(), -1
    for lv, _ in items:
        if lv == top:
            section += 1
        elif sub and lv == sub and section >= 0:
            split_sections.add(section)
    chapters: list[Chapter] = []
    section, part, part_only = -1, None, None
    for lv, text in items:
        if lv and lv < top:
            continue    # a single wrapper heading like "* The Book"
        if lv == top:
            section += 1
            part = text if section in split_sections else None
            chapters.append(Chapter(text, [text]))
            part_only = chapters[-1] if part else None   # holds only the part's heading so far
        elif part and lv == sub:
            if part_only is not None and chapters and chapters[-1] is part_only:
                chapters.pop()          # nothing between the part heading and its first chapter
                chapters.append(Chapter(part + PART_SEP + text, [part, text]))
            else:
                chapters.append(Chapter(part + PART_SEP + text, [text]))
            part_only = None
        else:
            if not chapters:
                chapters.append(Chapter("Opening", []))
            chapters[-1].paras.append(text)
            if chapters[-1] is part_only:
                part_only = None        # the part has an introduction of its own
    return chapters


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
