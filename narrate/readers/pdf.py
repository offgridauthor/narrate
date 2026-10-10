from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from narrate.book import PART_SEP, SPLIT, Book, Chapter, build_chapters
from narrate.common import Fatal


# ---------------------------------------------------------------- PDF

_PAGE_NUM = re.compile(r"^\W*(\d+|[ivxlcdm]+)\W*$", re.I)
_TERMINAL = tuple('.!?:;"”’)…]')


def read_pdf(path: Path) -> Book:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf  # older PyMuPDF
        except ImportError:
            raise Fatal("PDF input needs PyMuPDF: pip install pymupdf")
    doc = pymupdf.open(str(path))
    sizes: Counter = Counter()
    pages = []  # per page: (height, [block lines]) with lines (y0, y1, text, size)
    for page in doc:
        h = page.rect.height or 1
        blocks = []
        for b in page.get_text("dict", sort=True)["blocks"]:
            if b.get("type") != 0:
                continue
            lines = []
            for ln in b["lines"]:
                text = "".join(s["text"] for s in ln["spans"])
                if not text.strip():
                    continue
                size = max(s["size"] for s in ln["spans"])
                lines.append((ln["bbox"][1], ln["bbox"][3], text, size))
                for s in ln["spans"]:
                    sizes[round(s["size"], 1)] += len(s["text"])
            if lines:
                blocks.append(lines)
        pages.append((h, blocks))
    if not sizes:
        raise Fatal("this PDF has no text layer; OCR it first (ocrmypdf in.pdf out.pdf)")
    body = sizes.most_common(1)[0][0]

    def norm(t):
        return re.sub(r"\d+", "#", t.strip().lower())

    margin_counts: Counter = Counter()
    for h, blocks in pages:
        seen = set()
        for lines in blocks:
            for y0, y1, text, _ in lines:
                if y1 < 0.09 * h or y0 > 0.91 * h:
                    seen.add(norm(text))
        margin_counts.update(seen)
    repeat_floor = max(3, int(0.25 * len(pages)))

    def junk(line, h):
        y0, y1, text, _ = line
        in_margin = y1 < 0.09 * h or y0 > 0.91 * h
        return in_margin and (_PAGE_NUM.match(text.strip()) or margin_counts[norm(text)] >= repeat_floor)

    items = []  # (page, kind, text) with kind 'h' or 'p'
    for pno, (h, blocks) in enumerate(pages):
        for lines in blocks:
            lines = [ln for ln in lines if not junk(ln, h)]
            if not lines:
                continue
            avg = sum(ln[3] for ln in lines) / len(lines)
            if avg < 0.8 * body:
                continue  # footnotes, captions, small print
            text = ""
            for _, _, t, _ in lines:
                t = t.strip()
                if text.endswith("-") and len(text) > 1 and text[-2].isalpha() and t[:1].islower():
                    text = text[:-1] + t
                else:
                    text = f"{text} {t}" if text else t
            heading = max(ln[3] for ln in lines) >= 1.3 * body and len(text) < 120
            if (not heading and items and items[-1][1] == "p"
                    and not items[-1][2].endswith(_TERMINAL) and text[:1].islower()):
                prev = items[-1][2]
                joined = prev[:-1] + text if prev.endswith("-") else f"{prev} {text}"
                items[-1] = (items[-1][0], "p", joined)
            else:
                items.append((pno + 1, "h" if heading else "p", text))

    meta = doc.metadata or {}
    book = Book(meta.get("title") or path.stem, meta.get("author") or "")
    toc = [t for t in doc.get_toc() if len(t) >= 3]
    if toc:
        top = min(t[0] for t in toc)
        if SPLIT.get() == "parts":
            # bookmarks for chapters inside parts become chapters, titled with
            # their part; a part that starts on its first chapter's page merges in
            entries, part = [], None
            for lvl, title, page, *_ in toc:
                if lvl == top:
                    part = title
                    entries.append([page, title, True])
                elif lvl == top + 1 and part is not None:
                    if entries and entries[-1][2] and entries[-1][0] == page:
                        entries.pop()
                    entries.append([page, part + PART_SEP + title, False])
            marks = [(p, t) for p, t, _ in entries if p > 0]
        else:
            marks = [(t[2], t[1]) for t in toc if t[0] == top and t[2] > 0]
        if len(marks) >= 2:
            chapters = []
            for page, kind, text in items:
                while marks and page >= marks[0][0]:
                    chapters.append(Chapter(marks.pop(0)[1]))
                if not chapters:
                    chapters.append(Chapter("Front matter"))
                chapters[-1].paras.append(text)
            book.chapters = chapters
            return book
    heads = sum(1 for _, k, _ in items if k == "h")
    if 2 <= heads <= 400:
        book.chapters = build_chapters([(1 if k == "h" else None, t) for _, k, t in items], book.title)
    else:
        book.chapters = [Chapter(book.title, [t for _, _, t in items])]
    return book
