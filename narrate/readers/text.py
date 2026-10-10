from __future__ import annotations

import re
from pathlib import Path

from narrate.book import Book, build_chapters, is_separator, read_text_file
from narrate.common import SCENE


# ---------------------------------------------------------------- TXT

_TXT_CHAPTER = re.compile(
    r"^(chapter|part|book|prologue|epilogue|interlude|act|preface|introduction|afterword)\b.{0,60}$"
    r"|^([IVXLC]+|\d{1,3})\.?$", re.I)


def read_txt(path: Path) -> Book:
    text = read_text_file(path).replace("\r\n", "\n")
    if "\n\n" in text.strip():
        paras = [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    else:
        paras = [ln.strip() for ln in text.split("\n")]
    items = []
    for p in paras:
        if not p:
            continue
        if is_separator(p):
            items.append((None, SCENE))
        elif _TXT_CHAPTER.match(p):
            items.append((1, p))
        else:
            items.append((None, p))
    book = Book(path.stem)
    book.chapters = build_chapters(items, book.title)
    return book
