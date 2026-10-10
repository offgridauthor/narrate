from __future__ import annotations

import posixpath
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from narrate.book import Book, Chapter, clean
from narrate.common import SCENE


# ---------------------------------------------------------------- EPUB

_VOID = {"br", "hr", "img", "meta", "link", "input", "wbr", "col", "area",
         "base", "source", "embed", "param", "track"}
_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote",
          "pre", "dd", "dt", "td", "th", "tr", "section", "article",
          "figcaption", "caption", "header", "footer", "ul", "ol", "table"}
_SKIP_TAGS = {"script", "style", "head", "svg", "math", "nav", "aside", "rt"}
_NOTE_MARKS = ("footnote", "endnote", "rearnote", "noteref", "pagebreak",
               "doc-noteref", "doc-footnote", "doc-endnote", "doc-pagebreak")
_NOTE_SUP = re.compile(r"\s*[\[(]?[\d*†‡§¶,\s]+[\])]?\s*")


class _HTMLText(HTMLParser):
    """Collects (kind, text) blocks from XHTML: kind is 'p', 'h1'..'h6', or 'scene'."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[tuple[str, str]] = []
        self.buf: list[str] = []
        self.stack: list[tuple[str, bool]] = []
        self.skip = 0
        self.sup_at: list[int] = []

    def _flush(self, kind="p"):
        text = clean("".join(self.buf))
        self.buf = []
        if text:
            self.out.append((kind, text))

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if self.skip:
                return
            if tag == "br":
                self.buf.append(" ")
            elif tag == "hr":
                self._flush()
                self.out.append(("scene", ""))
            return
        a = {k: (v or "") for k, v in attrs}
        marks = (a.get("epub:type", "") + " " + a.get("role", "")).lower()
        skipping = bool(self.skip) or tag in _SKIP_TAGS or any(m in marks for m in _NOTE_MARKS)
        self.stack.append((tag, skipping))
        if skipping:
            self.skip += 1
            return
        if tag in _BLOCK:
            self._flush()
        if tag == "sup":
            self.sup_at.append(len(self.buf))

    def handle_endtag(self, tag):
        if not any(t == tag for t, _ in self.stack):
            return
        while self.stack:
            t, skipping = self.stack.pop()
            if skipping:
                self.skip -= 1
            elif t == "sup" and self.sup_at:
                start = self.sup_at.pop()
                if _NOTE_SUP.fullmatch("".join(self.buf[start:]) or "x"):
                    del self.buf[start:]
            elif t in _BLOCK:
                self._flush(t if re.fullmatch(r"h[1-6]", t) else "p")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def result(self):
        self._flush()
        return self.out


class _NavParser(HTMLParser):
    """Links inside the EPUB3 <nav epub:type="toc">."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.any_toc = False
        self.links: list[tuple[str, str]] = []
        self.href = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "nav":
            is_toc = "toc" in a.get("epub:type", "")
            if self.depth or is_toc or not self.any_toc:
                self.depth += 1
                self.any_toc = self.any_toc or is_toc
        elif tag == "a" and self.depth:
            self.href, self.text = a.get("href"), []

    def handle_endtag(self, tag):
        if tag == "nav" and self.depth:
            self.depth -= 1
        elif tag == "a" and self.href is not None:
            self.links.append((self.href, clean("".join(self.text))))
            self.href = None

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)


def read_epub(path: Path) -> Book:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//{*}rootfile").get("full-path")
    base = posixpath.dirname(rootfile)
    opf = ET.fromstring(z.read(rootfile))

    def resolve(href: str, rel_to: str = base) -> str:
        href = urllib.parse.unquote(href.split("#", 1)[0])
        return posixpath.normpath(posixpath.join(rel_to, href))

    meta = opf.find("{*}metadata")
    title = (meta.findtext("{*}title") or "") if meta is not None else ""
    author = (meta.findtext("{*}creator") or "") if meta is not None else ""
    manifest = {}
    for it in opf.iterfind(".//{*}item"):
        manifest[it.get("id")] = {
            "path": resolve(it.get("href", "")),
            "type": it.get("media-type", ""),
            "props": it.get("properties", "") or "",
        }

    labels: dict[str, str] = {}
    nav = next((m for m in manifest.values() if "nav" in m["props"].split()), None)
    if nav and nav["path"] in names:
        p = _NavParser()
        p.feed(z.read(nav["path"]).decode("utf-8", errors="replace"))
        for href, text in p.links:
            labels.setdefault(resolve(href, posixpath.dirname(nav["path"])), text)
    spine = opf.find("{*}spine")
    if not labels and spine is not None and spine.get("toc") in manifest:
        ncx_path = manifest[spine.get("toc")]["path"]
        if ncx_path in names:
            ncx = ET.fromstring(z.read(ncx_path))
            for np_ in ncx.iterfind(".//{*}navPoint"):
                src = np_.find("{*}content")
                text = np_.findtext("{*}navLabel/{*}text") or ""
                if src is not None:
                    labels.setdefault(resolve(src.get("src", ""), posixpath.dirname(ncx_path)), clean(text))

    docs = []
    for ref in (spine.iterfind("{*}itemref") if spine is not None else []):
        item = manifest.get(ref.get("idref"))
        if not item or ref.get("linear") == "no" or item["path"] not in names:
            continue
        if "html" not in item["type"] and not item["path"].endswith((".html", ".xhtml", ".htm")):
            continue
        docs.append(item["path"])

    use_toc = any(d in labels for d in docs)
    chapters: list[Chapter] = []
    for d in docs:
        parser = _HTMLText()
        parser.feed(z.read(d).decode("utf-8", errors="replace"))
        blocks = parser.result()
        if not any(k != "scene" for k, _ in blocks):
            continue
        heading = next((t for k, t in blocks if k.startswith("h")), None)
        starts = (d in labels) if use_toc else True
        if starts or not chapters:
            chapters.append(Chapter(labels.get(d) or heading or f"Section {len(chapters) + 1}"))
        chapters[-1].paras.extend(SCENE if k == "scene" else t for k, t in blocks)

    book = Book(title or path.stem, author, chapters)
    cover_id = None
    for m in opf.iterfind(".//{*}meta"):
        if m.get("name") == "cover":
            cover_id = m.get("content")
    cover = next((m for m in manifest.values() if "cover-image" in m["props"].split()), None) \
        or manifest.get(cover_id or "")
    if cover and cover["path"] in names and cover["type"].startswith("image/"):
        book.cover = z.read(cover["path"])
        book.cover_ext = "png" if "png" in cover["type"] else "jpg"
    return book
