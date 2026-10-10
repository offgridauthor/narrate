import json
import shutil
import subprocess

import pytest

from narrate.cli import main

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]


def test_profiles_json(config, capsys):
    assert main(["--config", str(config), "profiles", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    rows = {r["name"]: r for r in data["profiles"]}
    assert data["default"] == "dry-run"
    assert rows["cloud-voice"]["remote"] and rows["local-voice"]["remote"] is None


def test_outline(config, books, capsys):
    assert main(["--config", str(config), "outline", str(books / "book.md"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [c["title"] for c in data["chapters"]] == ["Chapter One", "Chapter Two", "Chapter Three"]


@needs_ffmpeg
def test_render_dry_run_m4b(config, books, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--config", str(config), "render", str(books / "book.txt"), "--out", str(out)]) == 0
    done = [e for e in events(capsys) if e["event"] == "done"][-1]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", done["output"]],
                           capture_output=True, text=True)
    assert [c["tags"]["title"] for c in json.loads(probe.stdout)["chapters"]] == ["Chapter 1", "Chapter 2", "IV"]


@needs_ffmpeg
def test_render_resumes_from_cache(config, books, tmp_path, capsys):
    out = tmp_path / "out"
    main(["--config", str(config), "render", str(books / "book.md"), "--out", str(out)])
    capsys.readouterr()
    main(["--config", str(config), "render", str(books / "book.md"), "--out", str(out)])
    start = [e for e in events(capsys) if e["event"] == "start"][0]
    assert start["cached"] == start["total"]


@needs_ffmpeg
def test_mp3_split_writes_one_file_per_chapter(config, books, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--config", str(config), "render", str(books / "book.md"), "--out", str(out),
                 "--format", "mp3-split", "--chapters", "1,3"]) == 0
    folder = [e for e in events(capsys) if e["event"] == "done"][-1]["output"]
    assert len(list((tmp_path / "out").glob("*/*.mp3"))) == 2, folder


def test_cloud_profile_without_key_falls_back(config, capsys, monkeypatch, tmp_path):
    # no Gemini key and no Breeze server here, so the chain ends at the dry run
    import narrate.engines.breeze as breeze
    monkeypatch.setattr(breeze.BreezeEngine, "healthy", lambda self: False)
    out = tmp_path / "a.wav"
    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")
    assert main(["--config", str(config), "audition", "--profile", "cloud-voice", "--out", str(out)]) == 0
    evs = events(capsys)
    assert [e["profile"] for e in evs if e["event"] == "fallback"] == ["cloud-voice", "local-voice"]
    assert [e for e in evs if e["event"] == "done"][-1]["profile"] == "dry-run"


@needs_ffmpeg
def test_unusable_cover_is_skipped(config, books, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["--config", str(config), "render", str(books / "badcover.epub"), "--out", str(out)]) == 0
    assert "cover image couldn't be embedded" in capsys.readouterr().err
    assert len(list(out.glob("*.m4b"))) == 1


def test_split_flag(config, books, capsys):
    assert main(["--config", str(config), "outline", str(books / "book.org"), "--json", "--split", "top"]) == 0
    assert len(json.loads(capsys.readouterr().out)["chapters"]) == 3
    config.write_text(config.read_text().replace("gpu_share = 1.0", 'gpu_share = 1.0\nsplit = "top"'))
    assert main(["--config", str(config), "outline", str(books / "book.org"), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["chapters"]) == 3
