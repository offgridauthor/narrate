from __future__ import annotations

import json

from narrate.audio import pcm_to_wav
from narrate.common import RATE, log
from narrate.engines.base import Engine, EngineUnavailable, Refused


class KokoroEngine(Engine):
    """Kokoro-82M, on this machine's GPU (or CPU). Profile keys: voice (required:
    one voice, or a weighted blend like "voice_a:0.6,voice_b:0.4"), speed,
    lang (defaults to the first voice's language letter), device."""
    name = "kokoro"
    max_chars = 1200
    uses_gpu = True

    def __init__(self, *a):
        super().__init__(*a)
        self.spec = str(self.prof.get("voice") or "")
        self.speed = float(self.prof.get("speed", 1.0))

    def identity(self):
        return json.dumps(["kokoro", self.spec, self.speed])

    def prepare(self):
        if not self.spec:
            raise EngineUnavailable("a kokoro profile needs `voice`, one voice name or a weighted blend")
        try:
            import torch
            from kokoro import KPipeline
        except ImportError as e:
            raise EngineUnavailable(f"kokoro is not installed ({e})")
        blend = []
        for part in self.spec.split(","):
            name, _, w = part.strip().partition(":")
            blend.append((name.strip(), float(w) if w else 1.0))
        lang = self.prof.get("lang") or blend[0][0][0]
        device = self.prof.get("device") or ("cuda" if torch.cuda.is_available() else "cpu")
        log(f"[{self.pname}] kokoro on {device}")
        self.pipe = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device=device)
        if len(blend) == 1:
            self.pack = blend[0][0]
        else:   # weighted blend; KPipeline accepts a voice tensor directly
            total = sum(w for _, w in blend)
            self.pack = sum(self.pipe.load_single_voice(n) * w for n, w in blend) / total

    def synth(self, text):
        import numpy as np
        pieces, last = [], None
        for r in self.pipe(text, voice=self.pack, speed=self.speed, split_pattern=r"\n+"):
            if r.audio is None:
                continue
            if last is not None and r.text_index != last:
                pieces.append(np.zeros(int(0.35 * RATE), dtype=np.float32))
            pieces.append(r.audio.detach().cpu().numpy().astype(np.float32))
            last = r.text_index
        if not pieces:
            raise Refused("kokoro produced no audio")
        audio = np.clip(np.concatenate(pieces), -1.0, 1.0)
        return pcm_to_wav((audio * 32767).astype("<i2").tobytes())
