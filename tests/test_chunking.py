from narrate.chunking import chunk_paras, split_long
from narrate.common import SCENE


def test_split_long_respects_limit():
    text = "One sentence here. " * 200
    parts = split_long(text, 300)
    assert len(parts) > 1
    assert all(len(p) <= 300 for p in parts)
    assert " ".join(parts).split() == text.split()


def test_chunks_pause_longer_at_scene_breaks():
    chunks = chunk_paras(["First paragraph.", SCENE, "Second paragraph."], 1000)
    assert [c.pause for c in chunks] == ["scene", "para"]
    assert [c.text for c in chunks] == ["First paragraph.", "Second paragraph."]
