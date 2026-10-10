from __future__ import annotations

import re
from pathlib import Path

from narrate.book import Book, build_chapters, is_separator, read_text_file
from narrate.common import SCENE


# ---------------------------------------------------------------- Markdown

def md_inline(s: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", s)
    s = re.sub(r"\[\^[^\]]+\]", "", s)
    s = re.sub(r"<https?://[^>]+>", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", r"\2", s)
    s = re.sub(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])", r"\2", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = s.replace("\\", "")
    return s


def read_md(path: Path) -> Book:
    text = read_text_file(path)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = text.splitlines()
    title = author = ""
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                for fm in lines[1:i]:
                    k, _, v = fm.partition(":")
                    if k.strip().lower() == "title":
                        title = v.strip().strip("\"'")
                    elif k.strip().lower() == "author":
                        author = v.strip().strip("\"'")
                lines = lines[i + 1:]
                break
    items: list = []
    para: list[str] = []
    fence = None

    def flush():
        if para:
            items.append((None, md_inline(" ".join(para))))
            para.clear()

    for i, raw in enumerate(lines):
        line = raw.rstrip()
        s = line.strip()
        if fence:
            if s.startswith(fence):
                fence = None
            continue
        if s.startswith("```") or s.startswith("~~~"):
            flush()
            fence = s[:3]
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", s)
        if m:
            flush()
            items.append((len(m.group(1)), md_inline(m.group(2))))
            continue
        if s and not para and re.fullmatch(r"=+", nxt or "x"):
            items.append((1, md_inline(s)))
            continue
        if s and not para and re.fullmatch(r"-{2,}", nxt or "x") and not is_separator(s):
            items.append((2, md_inline(s)))
            continue
        if re.fullmatch(r"=+|-{2,}", s) and items and items[-1][0] and not para:
            continue  # setext underline already consumed
        if not s:
            flush()
            continue
        if is_separator(s):
            flush()
            items.append((None, SCENE))
            continue
        if s.startswith("|") or re.match(r"^\[[^\]]+\]:\s", s) or re.match(r"^\[\^[^\]]+\]:", s):
            flush()
            continue
        s = re.sub(r"^(>\s?)+", "", s)
        if re.match(r"^([-*+]|\d+[.)])\s+", s):
            flush()
            s = re.sub(r"^([-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?", "", s)
        para.append(s)
    flush()
    book = Book(title or path.stem, author)
    book.chapters = build_chapters(items, book.title)
    return book
