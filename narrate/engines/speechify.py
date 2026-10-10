from __future__ import annotations

import base64
import json
import os

from narrate.audio import to_wav24
from narrate.engines.base import Engine, EngineUnavailable, RateLimited, Refused, Transient, _seconds, http_json


class SpeechifyEngine(Engine):
    """Speechify's text-to-speech API (paid). Profile keys: voice (required,
    a voice id from Speechify's catalog) and model. The key comes from
    SPEECHIFY_API_KEY."""
    name = "speechify"
    max_chars = 1500
    check_length = True
    remote = "Speechify's servers"
    URL = "https://api.speechify.ai/v1/audio/speech"
    DEFAULT_MODEL = "simba-3.2"

    def __init__(self, *a):
        super().__init__(*a)
        self.model = self.prof.get("model", self.DEFAULT_MODEL)
        self.voice = self.prof.get("voice")
        self.key = os.environ.get("SPEECHIFY_API_KEY")

    def prepare(self):
        if not self.voice:
            raise EngineUnavailable("a speechify profile needs `voice`, a voice id from Speechify's catalog")
        if not self.key:
            raise EngineUnavailable("no SPEECHIFY_API_KEY (or authinfo entry for api.speechify.ai)")

    def identity(self):
        return json.dumps(["speechify", self.model, self.voice])

    def synth(self, text):
        self.pace()
        status, j, hdr = http_json(self.URL, {"input": text, "voice_id": self.voice,
                                              "audio_format": "wav", "model": self.model},
                                   {"Authorization": f"Bearer {self.key}"})
        if status == 200:
            data = j.get("audio_data") or j.get("audio")
            if not data:
                raise Refused("speechify returned no audio")
            return to_wav24(base64.b64decode(data), "audio/" + str(j.get("audio_format", "wav")))
        msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else \
            (j.get("message") or j.get("error") or "")
        if status == 429:
            raise RateLimited(_seconds(hdr.get("Retry-After")) or 30, False, str(msg))
        if status in (401, 402, 403, 404):
            raise EngineUnavailable(f"speechify {status}: {msg}")
        if status >= 500:
            raise Transient(f"speechify {status}: {msg}")
        self.bad_request(status, msg)
