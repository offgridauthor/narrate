"""Example engine plugin: espeak-ng, a small offline synthesizer.

Copy this file into the plugins/ folder beside your profiles file, then use
it from a profile:

    [profiles.espeak]
    engine = "espeak"
    voice = "en-us"      # any voice from `espeak-ng --voices`
    speed = 165          # words per minute

It shows the whole engine interface: a name, prepare() to check the engine
can run here, identity() for the chunk cache, and synth() returning 24 kHz
mono 16-bit WAV."""

import json
import shutil
import subprocess

from narrate.audio import to_wav24
from narrate.engines import register
from narrate.engines.base import Engine, EngineUnavailable, Refused


@register
class EspeakEngine(Engine):
    name = "espeak"
    max_chars = 2000

    def __init__(self, *a):
        super().__init__(*a)
        self.voice = str(self.prof.get("voice", "en-us"))
        self.speed = int(self.prof.get("speed", 165))

    def prepare(self):
        if not shutil.which("espeak-ng"):
            raise EngineUnavailable("espeak-ng is not installed")

    def identity(self):
        return json.dumps(["espeak", self.voice, self.speed])

    def synth(self, text):
        r = subprocess.run(["espeak-ng", "-v", self.voice, "-s", str(self.speed), "--stdout", text],
                           capture_output=True)
        if r.returncode != 0 or not r.stdout:
            raise Refused(f"espeak-ng failed: {r.stderr.decode(errors='replace')[:200]}")
        return to_wav24(r.stdout, "audio/wav")
