from __future__ import annotations

import io
import os
import re
import subprocess
import wave
from pathlib import Path

from narrate.common import RATE


# ---------------------------------------------------------------- audio helpers

def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_pcm(data: bytes) -> tuple[bytes, int, int, int]:
    with wave.open(io.BytesIO(data)) as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels(), w.getsampwidth()


def ffmpeg_to_pcm(data: bytes) -> bytes:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                        "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"],
                       input=data, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("ffmpeg could not decode engine audio: " + r.stderr.decode(errors="replace")[-300:])
    return r.stdout


def to_wav24(data: bytes, mime: str | None = None) -> bytes:
    """Normalize engine output to 24 kHz mono s16 WAV."""
    mime = (mime or "").lower()
    if "l16" in mime or "pcm" in mime:
        m = re.search(r"rate=(\d+)", mime)
        rate = int(m.group(1)) if m else RATE
        data = pcm_to_wav(data, rate)  # L16 from Gemini is little-endian mono
        if rate == RATE:
            return data
    if data[:4] == b"RIFF":
        try:
            pcm, rate, ch, width = wav_pcm(data)
            if (rate, ch, width) == (RATE, 1, 2):
                return data
        except wave.Error:
            pass
    return pcm_to_wav(ffmpeg_to_pcm(data))


def wav_seconds(data_or_path) -> float:
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else str(data_or_path)
    with wave.open(src) as w:
        return w.getnframes() / w.getframerate()


def silence_pcm(seconds: float) -> bytes:
    return b"\x00\x00" * int(RATE * seconds)


def join_wavs(parts: list[bytes], gap: float = 0.15) -> bytes:
    pcm = silence_pcm(gap).join(wav_pcm(p)[0] for p in parts)
    return pcm_to_wav(pcm)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
