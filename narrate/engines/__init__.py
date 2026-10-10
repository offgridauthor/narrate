"""Speech engines. An engine turns a chunk of text into 24 kHz mono WAV.

Subclass `Engine` (see base.py), give it a `name`, and register it with
`register(cls)`; engines in the config folder's engines/ directory are loaded
at startup and can register themselves. Profiles pick an engine by name."""

from __future__ import annotations

from narrate.engines.breeze import BreezeEngine
from narrate.engines.gemini import GeminiEngine
from narrate.engines.kokoro import KokoroEngine
from narrate.engines.speechify import SpeechifyEngine
from narrate.engines.tone import ToneEngine


ENGINES = {cls.name: cls for cls in (GeminiEngine, BreezeEngine, KokoroEngine, SpeechifyEngine, ToneEngine)}


def register(cls) -> type:
    """Make engine class CLS available to profiles as `engine = "<cls.name>"`."""
    ENGINES[cls.name] = cls
    return cls
