from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from narrate.audio import to_wav24, write_atomic
from narrate.common import log
from narrate.engines.base import Engine, EngineUnavailable, RateLimited, Refused, Transient, _seconds, http_json
from narrate.waiting import wait_out


class GeminiEngine(Engine):
    """Gemini TTS through the Gemini API.

    Profile keys: model, voice (a prebuilt voice name), voice_id (a designed
    voice to use as is), design (a description to design a voice from, cached
    in voices.json beside the profiles file), style, gender, language.
    The key comes from GEMINI_API_KEY or GOOGLE_API_KEY."""
    name = "gemini"
    max_chars = 1800
    check_length = True
    remote = ("the Gemini API (on Google's free tier, Google may review the text "
              "and use it to improve its products)")
    API = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-3.8-flash-tts"

    def __init__(self, *a):
        super().__init__(*a)
        self.model = self.prof.get("model", self.DEFAULT_MODEL)
        self.voice = self.prof.get("voice")       # None lets the API pick its default
        self.design = (self.prof.get("design") or "").strip()
        self.style = (self.prof.get("style") or "").strip()
        self.key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.voice_id = self.voice

    def prepare(self):
        if not self.key:
            raise EngineUnavailable("no GEMINI_API_KEY (or authinfo entry for generativelanguage.googleapis.com)")
        if self.prof.get("voice_id"):         # a take picked with `tryout`
            self.voice_id = self.prof["voice_id"]
        elif self.design:
            self.voice_id = self.designed_voice() or self.voice

    def identity(self):
        return json.dumps(["gemini", self.model, self.voice_id, self.style])

    # designed voices are created once and cached by description in voices.json
    def _cache_path(self) -> Path:
        return Path(self.ctx["config_path"]).with_name("voices.json")

    def _voice_body(self, design: str) -> dict:
        voice = {"model": self.model, "type": "prompted", "display_name": f"narrate {self.pname}"[:60],
                 "language_code": self.prof.get("language", "en-US"), "prompted": {"input": design}}
        if self.prof.get("gender"):
            voice["gender"] = self.prof["gender"]
        return voice

    def new_voice(self, design: str) -> str:
        """Design a fresh voice from DESIGN without touching the cache (for tryouts).
        Each one takes a stored-voice slot; Google allows 200 per project, and
        unused ones expire after a year."""
        for attempt in range(1, 7):
            status, j, hdr = http_json(f"{self.API}/voices", {"store": True, "voice": self._voice_body(design)},
                                       {"x-goog-api-key": self.key})
            if status == 200:
                vid = j.get("id") or (j.get("voice") or {}).get("id") or str(j.get("name", "")).split("/")[-1]
                if not vid:
                    raise EngineUnavailable("voice design returned no id")
                return vid
            err = j.get("error") or {}
            msg = f"{status} {err.get('status', '')} {err.get('message', '')}".strip()
            if status == 429 and attempt < 4:
                wait_out(RateLimited(_seconds(hdr.get("Retry-After")), False, msg), attempt, True)
                continue
            if status == 429:
                raise EngineUnavailable(f"voice design still refused ({msg}); this can also mean the "
                                        "project's 200 stored voices are used up")
            if status >= 500:
                time.sleep(10 * attempt)
                continue
            raise EngineUnavailable(f"voice design failed ({msg})")
        raise Transient("voice design kept failing on Google's side")

    def designed_voice(self, refresh=False, preview: Path | None = None) -> str | None:
        cache_path = self._cache_path()
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        key = hashlib.sha1(f"{self.model}\n{self.design}".encode()).hexdigest()[:16]
        if key in cache and not refresh:
            return cache[key]["id"]
        status, j, _ = http_json(f"{self.API}/voices", {"store": True, "voice": self._voice_body(self.design)},
                                 {"x-goog-api-key": self.key})
        if status != 200:
            err = j.get("error") or {}
            msg = f"{status} {err.get('status', '')} {err.get('message', '')}".strip()
            log(f"[{self.pname}] voice design unavailable ({msg}); using {self._fallback_name()}")
            return None
        vid = j.get("id") or (j.get("voice") or {}).get("id") or str(j.get("name", "")).split("/")[-1]
        if not vid:
            log(f"[{self.pname}] voice design returned no id; using {self._fallback_name()}")
            return None
        cache[key] = {"id": vid, "profile": self.pname, "design": self.design,
                      "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        cache_path.write_text(json.dumps(cache, indent=2))
        # the guide shows sample_audio at the top level, the API reference under prompted
        pr = j.get("prompted") or {}
        sample = (j.get("sample_audio") or j.get("sampleAudio") or
                  pr.get("sample_audio") or pr.get("sampleAudio") or {})
        if preview and sample.get("data"):
            write_atomic(preview, to_wav24(base64.b64decode(sample["data"]),
                                           sample.get("mime_type") or sample.get("mimeType")))
        log(f"[{self.pname}] designed voice {vid}")
        return vid

    def _fallback_name(self) -> str:
        return f"prebuilt voice {self.voice}" if self.voice else "the API's default voice"

    def synth(self, text):
        self.pace()
        part = {"text": text}
        if self.style:
            part["speech_metadata"] = {"style": self.style}
        gen = {"responseModalities": ["AUDIO"]}
        if self.voice_id:
            gen["speechConfig"] = {"voiceConfig": {"voice": self.voice_id}}
        body = {"contents": [{"role": "user", "parts": [part]}], "generationConfig": gen}
        status, j, hdr = http_json(f"{self.API}/models/{self.model}:generateContent", body,
                                   {"x-goog-api-key": self.key})
        if status == 200:
            cands = j.get("candidates") or []
            parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
            for p in parts:
                d = p.get("inlineData") or p.get("inline_data")
                if d and d.get("data"):
                    return to_wav24(base64.b64decode(d["data"]), d.get("mimeType") or d.get("mime_type"))
            why = (cands[0].get("finishReason") if cands else None) or \
                  (j.get("promptFeedback") or {}).get("blockReason") or "no audio part"
            raise Refused(f"gemini returned no audio ({why})")
        err = j.get("error") or {}
        msg = err.get("message", "")
        if status == 429:
            delay, daily = None, False
            for d in err.get("details") or []:
                t = d.get("@type", "")
                if t.endswith("RetryInfo"):
                    delay = _seconds(d.get("retryDelay"))
                if t.endswith("QuotaFailure"):
                    for v in d.get("violations") or []:
                        if "PerDay" in (v.get("quotaId", "") + v.get("quotaMetric", "")):
                            daily = True
            raise RateLimited(delay or _seconds(hdr.get("Retry-After")), daily, msg)
        if status in (401, 403, 404):
            raise EngineUnavailable(f"gemini {status}: {msg}")
        if status >= 500:
            raise Transient(f"gemini {status}: {msg}")
        self.bad_request(status, msg)
