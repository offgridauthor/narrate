"""Write sample books in every supported format into the folder given as the
first argument. They exercise each reader's quirks: wrapper headings, nested
levels, scene breaks, notes, a cover, and long paragraphs. PDFs and the EPUB
cover need PyMuPDF and are skipped without it."""
import sys, zipfile
from pathlib import Path

try:
    import pymupdf
except ImportError:
    pymupdf = None

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
LONG = ("The road wound down through the cedar brakes toward the river, and every turn showed a little "
        "more of the valley: a tin roof, a windmill, a line of cottonwoods marking water. ") * 9
PARA = "She counted the steps to the gate and found, as always, that there were eleven."
DIAL = "“Are you coming?” he asked. “In a minute,” she said, and didn’t move."

(out / "book.md").write_text(f"""# The Book

## Chapter One

{PARA} See https://example.com for more, *emphasis* and **strong** and `code`.

{LONG}

* * *

{DIAL}

## Chapter Two

- a list item
- another [link text](https://example.com)

> a quoted line

{LONG}

## Chapter Three

{PARA}
""")

(out / "book.org").write_text(f"""#+TITLE: Org Book
#+AUTHOR: A. Writer

* Front
:PROPERTIES:
:ID: 1
:END:
{PARA}

* TODO Part One
** Chapter A
{LONG}

-----

{DIAL} with /italics/ and *bold* and [[https://x.org][a link]].

** Chapter B
1. first
2. second
#+begin_src python
print("skipped?")
#+end_src
{PARA}

* Part Two
** Chapter C
{LONG}
""")

(out / "book.txt").write_text(f"""Chapter 1

{PARA}

{LONG}

* * *

{DIAL}

Chapter 2

{LONG}

IV

{PARA}
""")

# DOCX: Heading1 parts, Heading2 chapters, a Title, an outlineLvl paragraph
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
def p(text, style=None, olvl=None):
    ppr = ""
    if style or olvl is not None:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:outlineLvl w:val="{olvl}"/>' if olvl is not None else "") + "</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
body = "".join([p("A Sample Book", "Title"), p("For testing"),
                p("Part One: Beginnings", "Heading1"), p("Chapter 1", "Heading2"), p(PARA), p(LONG),
                p("***"), p(DIAL), p("Chapter 2", "Heading2"), p(LONG),
                p("Part Two: Later", "Heading1"), p("Chapter 3", "Heading2"), p(PARA),
                p("An outline-level heading", None, 1), p(LONG)])
doc = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W}><w:body>{body}</w:body></w:document>'
styles = (f'<?xml version="1.0"?><w:styles {W}>'
          '<w:style w:styleId="Title"><w:name w:val="Title"/></w:style>'
          '<w:style w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
          '<w:style w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>')
core = ('<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Docx Title</dc:title><dc:creator>D. Author</dc:creator></cp:coreProperties>')
with zipfile.ZipFile(out / "book.docx", "w") as z:
    z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
    z.writestr("word/document.xml", doc)
    z.writestr("word/styles.xml", styles)
    z.writestr("docProps/core.xml", core)

# EPUB3 with nav, a footnote, an hr, a non-linear item, a cover
def xhtml(title, inner):
    return (f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f'<head><title>{title}</title></head><body>{inner}</body></html>')
with zipfile.ZipFile(out / "book.epub", "w") as z:
    z.writestr("mimetype", "application/epub+zip")
    z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    z.writestr("OEBPS/content.opf", '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Epub Title</dc:title><dc:creator>E. Author</dc:creator><meta name="cover" content="cov"/></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
<item id="c2b" href="c2b.xhtml" media-type="application/xhtml+xml"/><item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
<item id="cov" href="cover.png" media-type="image/png"/></manifest>
<spine><itemref idref="c1"/><itemref idref="c2"/><itemref idref="c2b"/><itemref idref="notes" linear="no"/></spine></package>''')
    z.writestr("OEBPS/nav.xhtml", xhtml("nav", '<nav epub:type="toc"><ol><li><a href="c1.xhtml">First</a></li><li><a href="c2.xhtml#x">Second</a></li></ol></nav>'))
    z.writestr("OEBPS/c1.xhtml", xhtml("c1", f'<h1>First</h1><p>{PARA}<sup><a epub:type="noteref" href="notes.xhtml#n1">1</a></sup></p><p>{LONG}</p><hr/><p>{DIAL}</p>'))
    z.writestr("OEBPS/c2.xhtml", xhtml("c2", f'<h2>Second</h2><p>{LONG}</p><aside epub:type="footnote">note</aside>'))
    z.writestr("OEBPS/c2b.xhtml", xhtml("c2b", f'<p>{PARA}<br/>{DIAL}</p>'))
    z.writestr("OEBPS/notes.xhtml", xhtml("notes", '<p>1. A note.</p>'))
    if pymupdf:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 32, 48), False)
        pix.set_rect(pix.irect, (120, 60, 30))
        z.writestr("OEBPS/cover.png", pix.tobytes("png"))

with zipfile.ZipFile(out / "badcover.epub", "w") as z:
    with zipfile.ZipFile(out / "book.epub") as src:
        for item in src.infolist():
            data = src.read(item.filename)
            z.writestr(item, b"\x89PNG\r\n\x1a\nnot really" if item.filename.endswith("cover.png") else data)

if pymupdf is None:
    sys.exit(0)

# PDF with running header, page numbers, a big heading per chapter, bookmarks
d = pymupdf.open()
toc = []
for ch in range(1, 4):
    for pg in range(2):
        page = d.new_page()
        page.insert_text((72, 40), "RUNNING HEADER", fontsize=9)
        y = 90
        if pg == 0:
            page.insert_text((72, y), f"Chapter {ch}", fontsize=22); y += 40
            toc.append([1, f"Chapter {ch}", len(d)])
        words = (LONG + " " + PARA).split()
        line = ""
        for w in words:
            if len(line) + len(w) > 80:
                page.insert_text((72, y), line, fontsize=11); y += 15; line = ""
                if y > 740: break
            line += w + " "
        page.insert_text((300, 800), str(len(d)), fontsize=9)
d.set_toc(toc)
d.save(out / "book.pdf")
d2 = pymupdf.open()   # a second PDF with no bookmarks, so headings come from type size
for ch in range(1, 3):
    page = d2.new_page(); page.insert_text((72, 90), f"Part {ch}", fontsize=24)
    page.insert_text((72, 140), PARA, fontsize=11); page.insert_text((300, 800), str(ch), fontsize=9)
d2.save(out / "nobookmarks.pdf")

# a PDF with parts holding chapters in its bookmarks; Part Two starts on its
# first chapter's page, Part One has a page of its own
d3 = pymupdf.open()
toc3 = []
for title, level in [("Part One", 1), ("Chapter 1", 2), ("Chapter 2", 2), ("Part Two", 1), ("Chapter 3", 2)]:
    if not (title == "Chapter 3"):
        page = d3.new_page()
        page.insert_text((72, 90), title, fontsize=22)
        page.insert_text((72, 140), PARA, fontsize=11)
    toc3.append([level, title, len(d3)])
d3.set_toc(toc3)
d3.save(out / "parts.pdf")
