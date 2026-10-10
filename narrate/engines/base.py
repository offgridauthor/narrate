from __future__ import annotations

import http.client
import io
import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

"""What every engine shares: the Engine interface, its errors, and HTTP helpers."""

class EngineUnavailable(Exception):
    """The engine cannot run here (no key, no package, no access)."""


class RateLimited(Exception):
    def __init__(self, delay: float | None, daily: bool = False, detail: str = ""):
        super().__init__(detail)
        self.delay, self.daily = delay, daily


class Refused(Exception):
    """This text produced no usable audio; splitting it may help."""


class Transient(Exception):
    """Network hiccup or server error; retry later."""


def http_json(url: str, body=None, headers=None, method=None, timeout=300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            j = json.loads(raw)
        except ValueError:
            j = {"error": {"message": raw.decode(errors="replace")[:500]}}
        return e.code, j, dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError,
            http.client.HTTPException) as e:
        raise Transient(str(e))


def _seconds(v) -> float | None:
    if v is None:
        return None
    m = re.match(r"^\s*([\d.]+)\s*s?\s*$", str(v))
    return float(m.group(1)) if m else None


class Engine:
    """One way of turning text into speech.

    A subclass sets `name` (what profiles put in `engine = ...`) and implements:

    - prepare(): check it can run here (keys, packages, servers) and raise
      EngineUnavailable if not; called once before the first chunk
    - identity(): a string naming the voice exactly; it keys the chunk cache,
      so anything that changes the sound belongs in it
    - synth(text): 24 kHz mono 16-bit WAV bytes for TEXT; raise Refused when
      this text can't be voiced, Transient for retryable failures, and
      RateLimited when the service asks to slow down

    Class attributes describe it to the rest of narrate:

    - max_chars: chunk size sent per call (a profile's `max_chars` overrides)
    - check_length: re-roll audio far too short or long for its text
    - uses_gpu: runs on this machine's GPU, so `gpu_share` applies and it's
      preferred for patching refused passages
    - remote: where the text goes when it leaves this machine, in words a
      person can weigh before sending a manuscript (None means it stays here)
    """
    name = "engine"
    max_chars = 1500
    check_length = False        # sanity-check audio duration against text length
    uses_gpu = False
    remote: str | None = None

    def __init__(self, profile_name: str, prof: dict, ctx: dict):
        self.pname, self.prof, self.ctx = profile_name, prof, ctx
        self.max_chars = int(prof.get("max_chars", self.max_chars))
        self.min_interval = float(prof.get("min_interval", 0))
        self.successes = 0
        self._last = 0.0

    def prepare(self) -> None:
        pass

    def identity(self) -> str:
        raise NotImplementedError

    def synth(self, text: str) -> bytes:
        raise NotImplementedError

    def pace(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def bad_request(self, status, message):
        """A 4xx before any success means the setup is wrong, not the text."""
        if self.successes == 0:
            raise EngineUnavailable(f"{self.name} rejected the first request ({status}): {message}")
        raise Refused(f"{status}: {message}")


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "narrate" + os.urandom(12).hex()
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        out.write(str(v).encode() + b"\r\n")
    for k, (fname, data, ctype) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
                  f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode())
        out.write(data + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def http_raw(url: str, data: bytes | None, ctype: str | None = None, timeout=900):
    headers = {"Content-Type": ctype} if ctype else {}
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError,
            http.client.HTTPException) as e:
        raise Transient(str(e))
