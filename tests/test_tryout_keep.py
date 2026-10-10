"""Tryout and keep with Breeze's server replaced by a stand-in."""
import json
import math
import struct
import tomllib
import urllib.parse

import pytest
import shutil

import narrate.engines.breeze as breeze
from narrate.cli import main

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


@pytest.fixture
def server(monkeypatch):
    calls = []
    known = []

    def pcm(text):
        n = max(12000, len(text) * 1600)
        return struct.pack(f"<{n}h", *(int(2000 * math.sin(i / 7)) for i in range(n)))

    def http_raw(url, data, ctype=None, timeout=900):
        if url.endswith("/v1/voices") and data is None:
            return 200, json.dumps([{"id": k} for k in known]).encode()
        if url.endswith("/v1/audio/speech"):
            fields = dict(urllib.parse.parse_qsl(data.decode()))
            calls.append(fields)
            return 200, pcm(fields["text"])
        vid = f"v{len(known)}"
        known.append(vid)
        return 200, json.dumps({"id": vid}).encode()

    monkeypatch.setattr(breeze, "http_raw", http_raw)
    monkeypatch.setattr(breeze.BreezeEngine, "healthy", lambda self: True)
    monkeypatch.setattr(breeze.BreezeEngine, "stop", lambda self: None)
    return calls


def test_tryout_then_keep(config, server, tmp_path, capsys):
    designs = tmp_path / "designs.txt"
    designs.write_text("# comment\nA calm voice.\nA bright voice. | quick\n")
    out = tmp_path / "try"
    assert main(["--config", str(config), "tryout", "--profile", "local-voice", "--designs", str(designs),
                 "--takes", "2", "--out", str(out)]) == 0
    manifest = json.loads((out / "tryout.json").read_text())
    assert [t["file"] for t in manifest["takes"]] == ["01-1.wav", "01-2.wav", "02-1.wav", "02-2.wav"]
    assert [t["seed"] for t in manifest["takes"]] == [42, 43, 42, 43]
    assert (out / "all.wav").exists() and (out / "02-2.ref.wav").exists()
    index = (out / "index.org").read_text()
    assert "2 descriptions, 2 takes each" in index and "A bright voice." in index
    # the design is spoken on the reference line, and the style rides along as direction
    designs_sent = [c for c in server if c.get("instruction") == "A bright voice."]
    assert designs_sent and designs_sent[0]["text"] == breeze.BreezeEngine.REF_LINE

    assert main(["--config", str(config), "keep", str(out), "--take", "02-2", "--as", "bright"]) == 0
    kept = tomllib.loads(config.read_text())["profiles"]["bright"]
    assert kept["engine"] == "breeze" and kept["seed"] == 43 and kept["style"] == "quick"
    assert kept["ref_text"] == breeze.BreezeEngine.REF_LINE
    assert (config.parent / kept["ref_audio"]).read_bytes() == (out / "02-2.ref.wav").read_bytes()

    # a name can be used once
    assert main(["--config", str(config), "keep", str(out), "--take", "01-1", "--as", "bright"]) == 1
