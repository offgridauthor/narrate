from __future__ import annotations

import atexit
import hashlib
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from narrate.audio import pcm_to_wav, to_wav24, write_atomic
from narrate.common import log
from narrate.paths import cache_dir, data_dir
from narrate.engines.base import Engine, EngineUnavailable, Refused, Transient, http_raw, multipart


class BreezeEngine(Engine):
    """Breeze TTS 2 through a local Breeze-TTS-2.cpp server.

    Voice design invents a new voice on every call, so a book voiced that way
    would wander. Instead the profile's voice is designed once, on a fixed
    reference line, saved as a cloned voice beside the profiles file, and
    every chunk is read in that saved voice.

    Profile keys: design, seed, cfg_scale, style, ref_audio and ref_text (clone
    from a clip instead of designing), ref_line (the line a design is spoken
    on), model (a .gguf; defaults to the only one in the data folder's
    models/), server, port, device ("cpu" to skip the GPU)."""
    name = "breeze"
    max_chars = 600            # the port's own split size; longer passes drift
    check_length = True
    uses_gpu = True
    REF_LINE = ("A voice reads this line once, at an easy pace, so that everything "
                "after it can be spoken in the same voice.")

    def __init__(self, *a):
        super().__init__(*a)
        home = data_dir()
        self.model = Path(self.prof["model"]).expanduser() if self.prof.get("model") \
            else self._only_model(home / "models")
        self.server = str(Path(self.prof.get("server") or shutil.which("breeze-server")
                               or home / "Breeze-TTS-2.cpp" / "build" / "breeze-server").expanduser())
        self.url = f"http://127.0.0.1:{int(self.prof.get('port', 8137))}"
        self.design = (self.prof.get("design") or "").strip()
        ra = self.prof.get("ref_audio")
        if ra:   # a relative path is relative to the profiles file
            rp = Path(ra).expanduser()
            ra = str(rp if rp.is_absolute() else Path(self.ctx["config_path"]).parent / rp)
        self.ref_audio = ra
        self.ref_text = self.prof.get("ref_text")
        self.ref_line = self.prof.get("ref_line") or self.REF_LINE
        self.style = (self.prof.get("style") or "").strip()
        self.seed = int(self.prof.get("seed", 42))
        self.cfg = float(self.prof.get("cfg_scale", 1.0))
        self.voices_dir = Path(self.ctx["config_path"]).with_name("breeze-voices")
        key = hashlib.sha1(f"{self.design}|{self.prof.get('ref_audio')}|{self.ref_text}|{self.seed}".encode()).hexdigest()[:8]
        self.voice = re.sub(r"[^A-Za-z0-9_-]", "-", f"narrate-{self.pname}")[:50] + f"-{key}"
        self.proc = None

    @staticmethod
    def _only_model(folder: Path) -> Path:
        """The one .gguf in FOLDER; with none or several, a path that explains
        itself when start_server reports it missing."""
        found = sorted(folder.glob("*.gguf")) if folder.is_dir() else []
        if len(found) == 1:
            return found[0]
        why = "no .gguf model" if not found else f"{len(found)} .gguf models; set `model` in the profile"
        return folder / f"({why})"

    def identity(self):
        return json.dumps(["breeze", self.model.name, self.voice, self.style, self.seed, self.cfg])

    def healthy(self) -> bool:
        try:
            status, body = http_raw(self.url + "/health", None, timeout=3)
            return status == 200 and b"ok" in body
        except Transient:
            return False

    def start_server(self):
        if not self.model.exists():
            raise EngineUnavailable(f"no Breeze model at {self.model}")
        if not Path(self.server).exists():
            raise EngineUnavailable(f"no breeze-server at {self.server}")
        self.voices_dir.mkdir(parents=True, exist_ok=True)
        logdir = cache_dir()
        logdir.mkdir(parents=True, exist_ok=True)
        cmd = [self.server, str(self.model), "--host", "127.0.0.1", "--port", self.url.rsplit(":", 1)[1],
               "--voices-dir", str(self.voices_dir), "--ws-port", "-1", "--chunk-max", "40"]
        if self.prof.get("device") == "cpu":
            cmd.append("--cpu")
        log(f"[{self.pname}] starting breeze-server; loading the model can take a minute")
        with open(logdir / "breeze-server.log", "ab") as out:
            self.proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)
        atexit.register(self.stop)
        deadline = time.time() + 300
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise EngineUnavailable(f"breeze-server exited ({self.proc.returncode}); "
                                        f"see {logdir / 'breeze-server.log'}")
            if self.healthy():
                return
            time.sleep(1)
        self.stop()
        raise EngineUnavailable("breeze-server didn't answer within five minutes")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def speak(self, fields: dict) -> bytes:
        body = urllib.parse.urlencode(fields).encode()
        for _ in range(60):
            status, data = http_raw(self.url + "/v1/audio/speech", body,
                                    "application/x-www-form-urlencoded")
            if status == 409:      # the server takes one request at a time
                time.sleep(5)
                continue
            if status == 200:
                return data[: len(data) // 2 * 2]
            msg = data.decode(errors="replace")[:300]
            if status == 404:
                raise EngineUnavailable(f"breeze lost voice {self.voice}: {msg}")
            if status >= 500:
                raise Transient(f"breeze {status}: {msg}")
            self.bad_request(status, msg)
        raise Transient("breeze-server stayed busy")

    def ensure_voice(self, preview: Path | None = None):
        self.voices_dir.mkdir(parents=True, exist_ok=True)   # a server started elsewhere skips start_server
        status, body = http_raw(self.url + "/v1/voices", None, timeout=30)
        known = {v.get("id") for v in json.loads(body or b"[]")} if status == 200 else set()
        ref_wav = self.voices_dir / f"{self.voice}.wav"
        if self.voice not in known:
            if self.ref_audio:
                wav, text = Path(self.ref_audio).expanduser().read_bytes(), self.ref_text
            else:
                if not ref_wav.exists():   # design the voice once, on the fixed line
                    log(f"[{self.pname}] designing a voice: {self.design}")
                    pcm = self.speak({"text": self.ref_line, "instruction": self.design,
                                      "seed": self.seed, "cfg_scale": self.cfg})
                    if not pcm:
                        raise EngineUnavailable("breeze produced no audio for the voice design")
                    write_atomic(ref_wav, pcm_to_wav(pcm))
                wav, text = ref_wav.read_bytes(), self.ref_line
            data, ctype = multipart({"ref_text": text, "name": self.voice},
                                    {"ref_audio": ("reference.wav", wav, "audio/wav")})
            status, body = http_raw(self.url + "/v1/voices", data, ctype, timeout=120)
            if status != 200:
                raise EngineUnavailable(f"breeze couldn't save the voice ({status}): {body[:200]!r}")
            log(f"[{self.pname}] saved Breeze voice {self.voice}")
        if preview is not None:
            src = ref_wav if ref_wav.exists() else Path(self.ref_audio or "").expanduser()
            if src.is_file():
                write_atomic(preview, to_wav24(src.read_bytes(), "audio/wav"))

    def reference_clip(self, design: str, seed: int) -> bytes:
        """The design step: DESIGN read on the fixed line, as a WAV to clone from."""
        pcm = self.speak({"text": self.ref_line, "instruction": design, "seed": seed, "cfg_scale": self.cfg})
        if not pcm:
            raise Refused("breeze produced no audio for the voice design")
        return pcm_to_wav(pcm)

    def trial_voice(self, design: str, seed: int) -> tuple[str, bytes]:
        """Design a voice the way ensure_voice does, but hold it in the server's
        memory only; the tryout keeps the reference clip so `keep` can save it."""
        ref = self.reference_clip(design, seed)
        data, ctype = multipart({"ref_text": self.ref_line},
                                {"ref_audio": ("reference.wav", ref, "audio/wav")})
        status, body = http_raw(self.url + "/v1/voices", data, ctype, timeout=120)
        if status != 200:
            raise EngineUnavailable(f"breeze couldn't hold the trial voice ({status}): {body[:200]!r}")
        return json.loads(body)["id"], ref

    def prepare(self):
        if not self.design and not (self.ref_audio and self.ref_text):
            raise EngineUnavailable("a breeze profile needs `design`, or `ref_audio` and `ref_text`")
        if not self.healthy():
            self.start_server()
        self.ensure_voice()

    def synth(self, text):
        if self.proc is not None and self.proc.poll() is not None:
            log(f"[{self.pname}] breeze-server stopped; restarting it")
            self.start_server()
        fields = {"text": text, "voice_id": self.voice, "seed": self.seed, "cfg_scale": self.cfg}
        if self.style:
            fields["instruction"] = self.style      # voice direction
        pcm = self.speak(fields)
        if not pcm:
            raise Refused("breeze returned no audio")
        return pcm_to_wav(pcm)
