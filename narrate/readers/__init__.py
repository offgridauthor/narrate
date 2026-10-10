"""Input readers. Each turns a file into a Book of chapters and paragraphs.

A reader is a function taking a Path and returning a Book. Register one for a
file extension with `register(".ext", func)`; readers in the config folder's
readers/ directory are loaded at startup and can register themselves."""

from __future__ import annotations

from pathlib import Path

from narrate.book import SPLIT, Book, finalize
from narrate.common import Fatal
from narrate.readers.docx import read_docx
from narrate.readers.epub import read_epub
from narrate.readers.markdown import read_md
from narrate.readers.org import read_org
from narrate.readers.pdf import read_pdf
from narrate.readers.text import read_txt


READERS = {".epub": read_epub, ".pdf": read_pdf, ".docx": read_docx, ".org": read_org,
           ".md": read_md, ".markdown": read_md, ".txt": read_txt}


def register(ext: str, func) -> None:
    """Read files ending in EXT (like ".rst") with FUNC(path) -> Book."""
    READERS[ext.lower()] = func


def load_book(path: Path, split: str = "parts") -> Book:
    """Read PATH with the reader for its extension. SPLIT is "parts" (a part's
    headed chapters each become chapters) or "top" (one chapter per part)."""
    ext = path.suffix.lower()
    if ext not in READERS:
        raise Fatal(f"unsupported input {ext or path.name}; use one of {', '.join(sorted(READERS))}")
    if not path.exists():
        raise Fatal(f"no such file: {path}")
    if split not in ("parts", "top"):
        raise Fatal(f"split must be parts or top, not {split!r}")
    token = SPLIT.set(split)
    try:
        return finalize(READERS[ext](path))
    finally:
        SPLIT.reset(token)
