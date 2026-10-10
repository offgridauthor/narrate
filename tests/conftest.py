"""Shared test setup: sample books in every format, and a profiles file."""

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


@pytest.fixture(scope="session")
def books(tmp_path_factory):
    out = tmp_path_factory.mktemp("books")
    subprocess.run([sys.executable, str(HERE / "make_books.py"), str(out)], check=True)
    return out


PROFILES = """
[defaults]
profile = "dry-run"
gpu_share = 1.0

[profiles.dry-run]
engine = "tone"
loudnorm = false

[profiles.local-voice]
engine = "breeze"
design = "A clear narrator."
fallback = ["dry-run"]

[profiles.cloud-voice]
engine = "gemini"
voice = "Some-Voice"
fallback = ["local-voice"]
"""


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A profiles file in its own config folder, with XDG folders inside tmp_path."""
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "SPEECHIFY_API_KEY", "NARRATE_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    conf = tmp_path / "conf" / "profiles.toml"
    conf.parent.mkdir()
    conf.write_text(PROFILES)
    return conf
