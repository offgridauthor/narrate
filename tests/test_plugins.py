import shutil
from pathlib import Path

import pytest

from narrate.cli import load_plugins
from narrate.engines import ENGINES
from narrate.readers import READERS, load_book

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "plugins"


def test_example_plugins_register(tmp_path):
    load_plugins(EXAMPLES)
    assert "espeak" in ENGINES
    assert ".html" in READERS
    page = tmp_path / "page.html"
    page.write_text("<h1>One</h1><p>First.</p><hr/><p>Second.</p><h1>Two</h1><p>Third.</p>")
    book = load_book(page)
    assert [c.title for c in book.chapters] == ["One", "Two"]


@pytest.mark.skipif(not shutil.which("espeak-ng"), reason="needs espeak-ng")
def test_espeak_engine_speaks(config):
    load_plugins(EXAMPLES)
    eng = ENGINES["espeak"]("espeak", {"voice": "en-us"}, {"config_path": str(config)})
    eng.prepare()
    wav = eng.synth("Hello there.")
    assert wav[:4] == b"RIFF" and len(wav) > 1000


def test_broken_plugin_does_not_stop_others(tmp_path, capsys):
    (tmp_path / "a_broken.py").write_text("raise RuntimeError('nope')")
    (tmp_path / "b_fine.py").write_text("from narrate.readers import register\nregister('.fine', lambda p: None)")
    load_plugins(tmp_path)
    assert ".fine" in READERS
    assert "a_broken.py" in capsys.readouterr().err
