from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from narrate.book import Book, build_chapters, clean


# ---------------------------------------------------------------- DOCX

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_docx(path: Path) -> Book:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    style_names = {}
    if "word/styles.xml" in names:
        for s in ET.fromstring(z.read("word/styles.xml")).iter(_W + "style"):
            n = s.find(_W + "name")
            style_names[s.get(_W + "styleId")] = (n.get(_W + "val") if n is not None else "") or ""
    title = author = ""
    if "docProps/core.xml" in names:
        core = ET.fromstring(z.read("docProps/core.xml"))
        title = core.findtext("{*}title") or ""
        author = core.findtext("{*}creator") or ""
    root = ET.fromstring(z.read("word/document.xml"))
    items = []
    for p in root.iter(_W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == _W + "t":
                parts.append(el.text or "")
            elif el.tag in (_W + "tab", _W + "br", _W + "cr"):
                parts.append(" ")
        text = clean("".join(parts))
        if not text:
            continue
        level = None
        ppr = p.find(_W + "pPr")
        if ppr is not None:
            ps = ppr.find(_W + "pStyle")
            sid = ps.get(_W + "val") if ps is not None else ""
            name = (style_names.get(sid) or sid or "").lower()
            m = re.match(r"heading\s*(\d)", name)
            if name == "title":
                title = title or text
                continue
            if m:
                level = int(m.group(1))
            else:
                ol = ppr.find(_W + "outlineLvl")
                if ol is not None and (ol.get(_W + "val") or "").isdigit() and int(ol.get(_W + "val")) < 9:
                    level = int(ol.get(_W + "val")) + 1
        items.append((level, text))
    book = Book(title or path.stem, author)
    book.chapters = build_chapters(items, book.title)
    return book
