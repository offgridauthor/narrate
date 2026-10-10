"""Where narrate keeps things, following the XDG base directory layout.

- config: profiles.toml, designed-voice caches, plugins (~/.config/narrate)
- data:   local engine installs and models (~/.local/share/narrate)
- cache:  logs, scratch audio, the live GPU-share setting (~/.cache/narrate)

NARRATE_CONFIG points at a profiles file anywhere; its folder then serves as
the config folder."""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "narrate"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache")


def default_config_path() -> Path:
    return Path(os.environ.get("NARRATE_CONFIG") or config_dir() / "profiles.toml").expanduser()
