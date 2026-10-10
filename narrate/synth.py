from __future__ import annotations

import hashlib
import time
from datetime import datetime
from pathlib import Path

from narrate.audio import join_wavs, pcm_to_wav, silence_pcm, wav_seconds
from narrate.chunking import halve
from narrate.common import Fatal, emit, log
from narrate.config import profile
from narrate.engines import ENGINES
from narrate.paths import cache_dir
from narrate.waiting import wait_out
from narrate.engines.base import Engine, EngineUnavailable, RateLimited, Refused, Transient


# ---------------------------------------------------------------- synthesis

class Synth:
    """Synthesizes text robustly with one engine: waits out quotas, retries
    hiccups, re-rolls implausible audio, splits refused text, and patches
    pieces that still fail with the next engine in the chain (or silence)."""

    def __init__(self, engine: Engine, patcher, no_wait: bool, workdir: Path):
        self.engine, self.patcher, self.no_wait, self.workdir = engine, patcher, no_wait, workdir
        self.streak = 0

    def plausible(self, wav: bytes, text: str) -> bool:
        if not self.engine.check_length or len(text) < 80:
            return True
        ratio = wav_seconds(wav) / (len(text) / 15.0)
        return 0.4 <= ratio <= 2.5

    def __call__(self, text: str) -> bytes:
        tries = rerolls = refusals = 0
        while True:
            try:
                wav = self.engine.synth(text)
                self.engine.successes += 1
                self.streak = 0
                if not self.plausible(wav, text) and rerolls < 2:
                    rerolls += 1
                    log(f"  implausible length ({wav_seconds(wav):.0f}s for {len(text)} chars); re-rolling")
                    continue
                return wav
            except RateLimited as e:
                self.streak += 1
                wait_out(e, self.streak, self.no_wait)
            except Transient as e:
                tries += 1
                if tries > 8:
                    raise Fatal(f"giving up after repeated errors: {e} (rerun to resume)")
                delay = min(600, 5 * 2 ** tries)
                log(f"  {e}; retrying in {delay}s")
                time.sleep(delay)
            except Refused as e:
                refusals += 1
                if refusals < 2:
                    continue
                halves = halve(text)
                if halves:
                    log(f"  {e}; splitting and retrying")
                    return join_wavs([self(h) for h in halves])
                return self.patch(text, str(e))

    def patch(self, text: str, why: str) -> bytes:
        excerpt = text[:120]
        with open(self.workdir / "patched.txt", "a") as f:
            f.write(f"{datetime.now():%F %T}\t{why}\t{text}\n")
        patcher = self.patcher() if self.patcher else None
        if patcher:
            try:
                emit("patched", text=excerpt, engine=patcher.pname, reason=why)
                return patcher.synth(text)
            except Exception as e:  # noqa: BLE001
                log(f"  patch engine failed too: {e}")
        emit("patched", text=excerpt, engine="silence", reason=why)
        return pcm_to_wav(silence_pcm(0.6))


def chunk_path(workdir: Path, identity: str, text: str) -> Path:
    h = hashlib.sha1(f"{identity}\n{text}".encode()).hexdigest()[:20]
    return workdir / "chunks" / f"{h}.wav"


GPU_SHARE_FILE = cache_dir() / "gpu-share"


def gpu_share(prof: dict) -> float:
    """How much of the time a local render keeps the GPU busy. The file, written
    by narrate-gpu-share in Emacs, wins over the profile, and it's read before
    every chunk, so a change reaches a render that's already going."""
    v = prof.get("gpu_share", 1.0)
    try:
        v = float(GPU_SHARE_FILE.expanduser().read_text().strip())
    except (OSError, ValueError):
        pass
    try:
        return min(1.0, max(0.05, float(v)))
    except (TypeError, ValueError):
        return 1.0


def make_engine(cfg, ctx, name) -> Engine:
    prof = profile(cfg, name)
    cls = ENGINES.get(prof.get("engine", ""))
    if not cls:
        raise Fatal(f"profile {name!r} has unknown engine {prof.get('engine')!r}")
    return cls(name, prof, ctx)


def first_ready(cfg, ctx, names: list[str], announce=True) -> tuple[Engine | None, list[str]]:
    """Prepare the first engine in names that can run; return it and the rest."""
    for i, n in enumerate(names):
        eng = make_engine(cfg, ctx, n)
        try:
            eng.prepare()
            return eng, names[i + 1:]
        except EngineUnavailable as e:
            if announce:
                emit("fallback", profile=n, reason=str(e))
            log(f"[{n}] unavailable: {e}")
    return None, []


def lazy_patcher(cfg, ctx, names: list[str]):
    """Engine for pieces the main engine refuses: local engines first,
    prepared only if a refusal actually happens."""
    state = {}

    def get():
        if "eng" not in state:
            local = [n for n in names
                     if getattr(ENGINES.get(cfg["profiles"].get(n, {}).get("engine")), "uses_gpu", False)]
            ordered = local + [n for n in names if n not in local]
            state["eng"] = first_ready(cfg, ctx, ordered, announce=False)[0] if ordered else None
        return state["eng"]
    return get
