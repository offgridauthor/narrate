from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from narrate.audio import pcm_to_wav, silence_pcm, wav_seconds, write_atomic
from narrate.book import Book
from narrate.common import Fatal, RATE, emit, log


# ---------------------------------------------------------------- assembly

def ffmeta_escape(s: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", s)


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for n in range(2, 1000):
        q = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not q.exists():
            return q
    raise Fatal(f"too many existing outputs like {p}")


def safe_name(s: str, n: int = 80) -> str:
    s = re.sub(r"[/\\:*?\"<>|\x00-\x1f]", "", s).strip(" .")
    return (s[:n].rstrip() or "untitled")


def audio_filter(prof: dict) -> str:
    parts = []
    tempo = float(prof.get("tempo", 1.0))
    if abs(tempo - 1.0) > 0.001:
        parts.append(f"atempo={tempo:g}")   # pitch-preserving speed change
    if prof.get("filter"):
        parts.append(prof["filter"])
    if prof.get("loudnorm", True):
        parts.append("loudnorm=I=-18:TP=-2:LRA=11")
    return ",".join(parts) or "anull"


def run_ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Fatal("ffmpeg failed: " + r.stderr.strip()[-800:])


def assemble(book: Book, chapters: list, plan: list, prof: dict, pname: str,
             out_dir: Path, workdir: Path) -> Path:
    gaps = {}
    for kind in ("paragraph", "scene", "chapter"):
        g = workdir / f"gap-{kind}.wav"
        write_atomic(g, pcm_to_wav(silence_pcm(float(prof[f"gap_{kind}"]))))
        gaps[kind] = g
    fmt = prof.get("format", "m4b")
    bitrate = str(prof.get("bitrate", "96k"))
    af = audio_filter(prof)
    base = safe_name(f"{book.title} - {pname}")
    cover = None
    if book.cover:
        cover = workdir / f"cover.{book.cover_ext}"
        write_atomic(cover, book.cover)

    def listing(paths: list[Path], name: str) -> Path:
        lst = workdir / name
        lst.write_text("".join(f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                               for p in paths))
        return lst

    def chapter_files(ci: int, last: bool) -> list[Path]:
        files = []
        for path, pause in plan[ci]:
            files.append(path)
            files.append(gaps["scene" if pause == "scene" else "paragraph"])
        files[-1] = gaps["chapter"] if not last else files[-1]
        return files

    emit("assembling", format=fmt)
    if fmt == "mp3-split":
        final = unique_path(out_dir / base)
        folder = final.with_name(final.name + ".part")   # renamed once every chapter is written
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        for ci, ch in enumerate(chapters):
            lst = listing(chapter_files(ci, True), f"concat-{ci:03d}.txt")
            name = folder / f"{ci + 1:02d} - {safe_name(ch.title, 60)}.mp3"
            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-af", af,
                        "-c:a", "libmp3lame", "-b:a", bitrate, "-ar", str(RATE), "-ac", "1",
                        "-id3v2_version", "3", "-metadata", f"title={ch.title}",
                        "-metadata", f"album={book.title}", "-metadata", f"artist={book.author}",
                        "-metadata", f"track={ci + 1}/{len(chapters)}", str(name)])
        if cover:
            shutil.copyfile(cover, folder / f"cover.{book.cover_ext}")
        os.replace(folder, final)
        return final

    files, starts, t = [], [], 0.0
    for ci in range(len(chapters)):
        starts.append(t)
        cf = chapter_files(ci, ci == len(chapters) - 1)
        files.extend(cf)
        t += sum(wav_seconds(p) for p in cf)
    lst = listing(files, "concat.txt")
    meta = [";FFMETADATA1", f"title={ffmeta_escape(book.title)}", f"album={ffmeta_escape(book.title)}",
            f"artist={ffmeta_escape(book.author)}", "genre=Audiobook",
            f"comment={ffmeta_escape('narrated with ' + pname)}"]
    for ci, ch in enumerate(chapters):
        end = starts[ci + 1] if ci + 1 < len(chapters) else t
        meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(starts[ci] * 1000)}",
                 f"END={int(end * 1000)}", f"title={ffmeta_escape(ch.title)}"]
    meta_path = workdir / "ffmetadata.txt"
    meta_path.write_text("\n".join(meta) + "\n")

    ext = "mp3" if fmt == "mp3" else "m4b"
    out = unique_path(out_dir / f"{base}.{ext}")

    def ffmpeg_args(with_cover: bool) -> list[str]:
        args = ["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(meta_path)]
        if with_cover:
            args += ["-i", str(cover)]
        args += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if with_cover:
            args += ["-map", "2:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        args += ["-af", af, "-ar", str(RATE), "-ac", "1"]
        if ext == "mp3":
            args += ["-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3", "-f", "mp3"]
        else:
            args += ["-c:a", "aac", "-b:a", bitrate, "-movflags", "+faststart", "-f", "mp4"]
        return args + [str(out)]

    # write under a temporary name, so an interrupted assembly never leaves a
    # half-written book that looks finished
    final, out = out, out.with_name(out.name + ".part")
    try:
        try:
            run_ffmpeg(ffmpeg_args(bool(cover)))
        except Fatal as e:
            if not cover:
                raise
            # a damaged or unusual cover image shouldn't cost the whole book
            log(f"the cover image couldn't be embedded ({str(e)[-160:]}); writing the book without it")
            out.unlink(missing_ok=True)
            run_ffmpeg(ffmpeg_args(False))
        os.replace(out, final)
    finally:
        out.unlink(missing_ok=True)
    return final
