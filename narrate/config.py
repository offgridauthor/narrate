from __future__ import annotations

import tomllib
from pathlib import Path


from narrate.common import Fatal


# ---------------------------------------------------------------- config

DEFAULTS = {"format": "m4b", "bitrate": "96k", "loudnorm": True,
            "gap_paragraph": 0.45, "gap_scene": 1.4, "gap_chapter": 2.2}


def load_config(path: Path) -> dict:
    if not path.exists():
        raise Fatal(f"no profiles file at {path}")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("defaults", {})
    cfg.setdefault("profiles", {})
    return cfg


def profile(cfg: dict, name: str) -> dict:
    if name not in cfg["profiles"]:
        raise Fatal(f"no profile named {name!r} in profiles file")
    return {**DEFAULTS, **cfg["defaults"], **cfg["profiles"][name]}


def chain(cfg: dict, name: str) -> list[str]:
    """The profile, then its fallbacks (and theirs), without repeats."""
    out, todo = [], [name]
    while todo:
        n = todo.pop(0)
        if n in out:
            continue
        out.append(n)
        fb = cfg["profiles"].get(n, {}).get("fallback", [])
        todo.extend([fb] if isinstance(fb, str) else fb)
    return out
