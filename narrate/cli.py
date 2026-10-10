"""narrate: turn ebooks and documents into chaptered audiobooks.

Backend for narrate.el, and usable from a shell:

  narrate profiles [--json]
  narrate outline FILE [--json] [--text] [--split parts|top]
  narrate extract FILE OUT.org [--split parts|top]
  narrate audition --profile NAME [--text-file F] --out OUT.wav
  narrate design --profile NAME --out PREVIEW.wav [--refresh]
  narrate tryout --profile NAME --out DIR [--designs FILE] [--takes N]
                 [--text-file F] [--tempo T]
  narrate keep TRYOUT_DIR --take 01-3 --as NAME
  narrate render FILE --profile NAME [--chapters 1,3-5] [--out DIR]
                 [--format m4b|mp3|mp3-split] [--split parts|top] [--no-wait]

Inputs: .epub .pdf .txt .docx .org .md, plus any a plugin registers.
Engines: gemini, breeze, kokoro, speechify, tone (a dry run), plus any a
plugin registers. Profiles in the profiles file choose among them.

Machine-readable events go to stdout as JSON lines ({"event": ...});
human-readable log lines go to stderr.
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from narrate.commands import cmd_audition, cmd_design, cmd_extract, cmd_keep, cmd_outline, cmd_profiles, cmd_render, cmd_tryout
from narrate.common import Fatal, emit, log
from narrate.config import load_config
from narrate.paths import default_config_path


def load_plugins(folder: Path) -> None:
    """Import every .py in FOLDER; plugins register engines and readers with
    narrate.engines.register and narrate.readers.register."""
    import importlib.util
    for f in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        try:
            spec = importlib.util.spec_from_file_location(f"narrate_plugin_{f.stem}", f)
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
        except Exception as e:  # noqa: BLE001  a broken plugin shouldn't stop the rest
            log(f"narrate: plugin {f.name} didn't load: {e}")


def _on_term(*_):
    raise KeyboardInterrupt


def main(argv=None):
    ap = argparse.ArgumentParser(prog="narrate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=None, help="profiles TOML (default: $NARRATE_CONFIG, else ~/.config/narrate/profiles.toml)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("render")
    p.add_argument("--split", choices=["parts", "top"],
                   help="parts: a part's headed chapters each become chapters (default); top: one per part")
    p.add_argument("input")
    p.add_argument("--profile", default=None)
    p.add_argument("--chapters")
    p.add_argument("--out")
    p.add_argument("--format", choices=["m4b", "mp3", "mp3-split"])
    p.add_argument("--no-wait", action="store_true", help="exit (code 75) at the daily quota instead of waiting")
    p = sub.add_parser("outline")
    p.add_argument("--split", choices=["parts", "top"],
                   help="parts: a part's headed chapters each become chapters (default); top: one per part")
    p.add_argument("input")
    p.add_argument("--json", action="store_true")
    p.add_argument("--text", action="store_true")
    p = sub.add_parser("extract")
    p.add_argument("--split", choices=["parts", "top"],
                   help="parts: a part's headed chapters each become chapters (default); top: one per part")
    p.add_argument("input")
    p.add_argument("output")
    p = sub.add_parser("audition")
    p.add_argument("--profile", default=None)
    p.add_argument("--text-file")
    p.add_argument("--out", required=True)
    p = sub.add_parser("design")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("tryout", help="several designed voices, several takes each, to compare")
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--designs", help="text file, one description per line, optional ' | style'")
    p.add_argument("--takes", type=int, default=3)
    p.add_argument("--text-file")
    p.add_argument("--tempo", type=float, help="override the profile's tempo for this tryout")
    p = sub.add_parser("keep", help="save one tryout take as a profile of its own")
    p.add_argument("dir", help="the tryout folder")
    p.add_argument("--take", required=True, help="the take's file name, like 01-3")
    p.add_argument("--as", dest="name", required=True, help="name for the new profile")
    p = sub.add_parser("profiles")
    p.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    signal.signal(signal.SIGTERM, _on_term)
    load_plugins(config_path.parent / "plugins")
    try:
        cfg = load_config(config_path)
        if getattr(args, "profile", "x") is None:
            args.profile = cfg["defaults"].get("profile") or next(iter(cfg["profiles"]), None)
        ctx = {"config_path": str(config_path)}
        {"render": cmd_render, "outline": cmd_outline, "extract": cmd_extract,
         "audition": cmd_audition, "design": cmd_design, "tryout": cmd_tryout,
         "keep": cmd_keep, "profiles": cmd_profiles}[args.cmd](args, cfg, ctx)
    except Fatal as e:
        emit("error", message=str(e))
        log(f"narrate: {e}")
        return 1
    except KeyboardInterrupt:
        emit("paused", reason="interrupted")
        log("narrate: stopped; rerun to resume from the cache")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
