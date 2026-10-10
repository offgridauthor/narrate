"""Example reader plugin: plain HTML files, one chapter per <h1>/<h2>.

Copy this file into the plugins/ folder beside your profiles file, and
narrate opens .html and .htm files like any other book. A reader is a
function from a path to a Book; register() ties it to file extensions."""

from pathlib import Path

from narrate.book import Book, build_chapters, read_text_file
from narrate.common import SCENE
from narrate.readers import register
from narrate.readers.epub import _HTMLText


def read_html(path: Path) -> Book:
    parser = _HTMLText()
    parser.feed(read_text_file(path))
    items = []
    for kind, text in parser.result():
        if kind == "scene":
            items.append((None, SCENE))
        elif kind in ("h1", "h2"):
            items.append((int(kind[1]), text))
        else:
            items.append((None, text))
    book = Book(path.stem)
    book.chapters = build_chapters(items, book.title)
    return book


register(".html", read_html)
register(".htm", read_html)
