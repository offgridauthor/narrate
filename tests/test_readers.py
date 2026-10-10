import pytest

from narrate.common import SCENE
from narrate.readers import READERS, load_book

EXPECTED = {
    "book.md": ("book", ["Chapter One", "Chapter Two", "Chapter Three"]),
    "book.org": ("Org Book", ["Front", "Part One \u2014 Chapter A", "Part One \u2014 Chapter B",
                              "Part Two \u2014 Chapter C"]),
    "book.txt": ("book", ["Chapter 1", "Chapter 2", "IV"]),
    "book.docx": ("Docx Title", ["Opening", "Part One: Beginnings \u2014 Chapter 1",
                                 "Part One: Beginnings \u2014 Chapter 2", "Part Two: Later \u2014 Chapter 3",
                                 "Part Two: Later \u2014 An outline-level heading"]),
    "book.epub": ("Epub Title", ["First", "Second"]),
    "book.pdf": ("book", ["Chapter 1", "Chapter 2", "Chapter 3"]),
    "nobookmarks.pdf": ("nobookmarks", ["Part 1", "Part 2"]),
    "parts.pdf": ("parts", ["Part One", "Part One \u2014 Chapter 1", "Part One \u2014 Chapter 2",
                            "Part Two \u2014 Chapter 3"]),
}

ONE_PER_PART = {
    "book.org": ["Front", "Part One", "Part Two"],
    "book.docx": ["Opening", "Part One: Beginnings", "Part Two: Later"],
    "parts.pdf": ["Part One", "Part Two"],
}


@pytest.mark.parametrize("name", sorted(ONE_PER_PART))
def test_one_chapter_per_part(books, name):
    if not (books / name).exists():
        pytest.skip("PDF support needs PyMuPDF")
    assert [c.title for c in load_book(books / name, "top").chapters] == ONE_PER_PART[name]


def test_part_heading_is_read_with_its_first_chapter(books):
    chapters = load_book(books / "book.org").chapters
    assert chapters[1].paras[:2] == ["Part One", "Chapter A"]
    assert chapters[2].paras[0] == "Chapter B"


def test_part_with_an_introduction_keeps_it(tmp_path):
    f = tmp_path / "intro.md"
    f.write_text("# Part One\n\nA word before the chapters.\n\n## The First\n\nText.\n\n"
                 "# Part Two\n\n## The Second\n\nMore.\n")
    titles = [c.title for c in load_book(f).chapters]
    assert titles == ["Part One", "Part One \u2014 The First", "Part Two \u2014 The Second"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_chapters(books, name):
    if not (books / name).exists():
        pytest.skip("PDF support needs PyMuPDF")
    book = load_book(books / name)
    title, chapters = EXPECTED[name]
    assert book.title == title
    assert [c.title for c in book.chapters] == chapters
    assert all(c.paras for c in book.chapters)


def test_scene_breaks_become_markers(books):
    book = load_book(books / "book.md")
    assert SCENE in book.chapters[0].paras
    assert not any(p.strip() == "* * *" for c in book.chapters for p in c.paras)


def test_urls_and_footnotes_dropped(books):
    md = load_book(books / "book.md")
    assert not any("https://" in p for c in md.chapters for p in c.paras)
    epub = load_book(books / "book.epub")
    assert not any(p.endswith("eleven.1") for c in epub.chapters for p in c.paras)


def test_unknown_format(tmp_path):
    from narrate.common import Fatal
    f = tmp_path / "x.xyz"
    f.write_text("hello")
    with pytest.raises(Fatal):
        load_book(f)


def test_every_reader_registered():
    assert {".epub", ".pdf", ".docx", ".org", ".md", ".markdown", ".txt"} <= set(READERS)
