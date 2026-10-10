from __future__ import annotations

import re
from pathlib import Path

from narrate.book import Book, build_chapters, is_separator, read_text_file
from narrate.common import SCENE


# ---------------------------------------------------------------- Org

_ORG_HEAD = re.compile(r"^(\*+)\s+(.*?)\s*$")
_ORG_KEYWORDS = {"TODO", "DONE", "NEXT", "WAIT", "WAITING", "HOLD", "STARTED",
                 "SOMEDAY", "CANCELLED", "CANCELED", "PROJ", "IDEA", "DRAFT"}
_ORG_LINK = re.compile(r"\[\[([^\]]+)\](?:\[([^\]]*)\])?\]")
_ORG_EMPH = re.compile(r"(^|[\s(\-'\"{\u2018\u201c])([*/_=~+])(\S|\S.*?\S)\2(?=[\s\-.,:;!?'\")}\]\u2019\u201d]|$)")
_LIST = re.compile(r"^\s*(?:[-+]|\d+[.)]|\s\*)\s+(?:\[[ X-]\]\s+)?")


def org_inline(s: str) -> str:
    s = _ORG_LINK.sub(lambda m: m.group(2) or ("" if re.match(r"\w+:", m.group(1)) else m.group(1)), s)
    s = re.sub(r"\[fn:[^\]]*\]", "", s)
    s = re.sub(r"[<\[]\d{4}-\d\d-\d\d[^>\]]*[>\]]", "", s)
    for _ in range(2):
        s = _ORG_EMPH.sub(lambda m: m.group(1) + m.group(3), s)
    return s


def read_org(path: Path) -> Book:
    lines = read_text_file(path).splitlines()
    title = author = ""
    items: list = []
    para: list[str] = []
    block = None          # current #+begin_ name
    skip_level = None     # headline level of a COMMENT / :noexport: subtree
    in_drawer = False

    def flush():
        if para:
            items.append((None, org_inline(" ".join(para))))
            para.clear()

    for raw in lines:
        line = raw.rstrip()
        m = _ORG_HEAD.match(line)
        if block is None and is_separator(line):
            flush()
            items.append((None, SCENE))
            continue
        if m and block is None:
            level = len(m.group(1))
            if skip_level is not None and level > skip_level:
                continue
            skip_level = None
            flush()
            raw_head = m.group(2)
            if raw_head.startswith("COMMENT") or ":noexport:" in raw_head or raw_head == "Footnotes":
                skip_level = level
                continue
            text = re.sub(r"\s+:[\w@#%:]+:\s*$", "", raw_head)
            words = text.split(" ", 1)
            if words[0] in _ORG_KEYWORDS:
                text = words[1] if len(words) > 1 else ""
            text = re.sub(r"^\[#[A-Z0-9]\]\s*", "", text)
            text = re.sub(r"\s*\[\d+/\d+\]|\s*\[\d+%\]", "", text)
            items.append((level, org_inline(text)))
            continue
        if skip_level is not None:
            continue
        low = line.strip().lower()
        if block:
            if low.startswith("#+end_"):
                block = None
                flush()
            elif block in ("src", "example", "export", "comment", "latex", "html"):
                pass
            elif block == "verse":
                if low:
                    items.append((None, org_inline(line.strip())))
            else:
                if low:
                    para.append(line.strip())
                else:
                    flush()
            continue
        if low.startswith("#+begin_"):
            flush()
            block = low[8:].split()[0] if low[8:].split() else "x"
            continue
        if low.startswith("#+title:"):
            title = line.split(":", 1)[1].strip()
            continue
        if low.startswith("#+author:"):
            author = line.split(":", 1)[1].strip()
            continue
        if re.match(r"^\s*:[\w-]+:\s*$", line):
            in_drawer = low != ":end:"
            continue
        if in_drawer:
            continue
        if (low.startswith("#+") or re.match(r"^\s*#(\s|$)", line) or low.startswith("|")
                or re.match(r"^\s*(scheduled|deadline|closed):", low) or re.match(r"^\[fn:", line)):
            flush()
            continue
        if not low:
            flush()
            continue
        if _LIST.match(line):
            flush()
            para.append(_LIST.sub("", line))
            continue
        para.append(line.strip())
    flush()
    book = Book(title or path.stem, author)
    book.chapters = build_chapters(items, book.title)
    return book
