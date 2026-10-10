from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

from narrate.assemble import assemble, audio_filter, run_ffmpeg, safe_name, unique_path
from narrate.audio import join_wavs, wav_seconds, write_atomic
from narrate.book import clean
from narrate.chunking import chunk_paras, split_long
from narrate.common import CHARS_PER_MINUTE, Fatal, RATE, SCENE, emit, log
from narrate.config import DEFAULTS, chain, profile
from narrate.engines import ENGINES
from narrate.engines.base import EngineUnavailable, Refused, Transient
from narrate.engines.breeze import BreezeEngine
from narrate.engines.gemini import GeminiEngine
from narrate.readers import load_book
from narrate.synth import Synth, chunk_path, first_ready, gpu_share, lazy_patcher, make_engine


# ---------------------------------------------------------------- commands

def split_setting(args, cfg) -> str:
    """--split on the command line, else `split` in the profiles file's [defaults]."""
    return getattr(args, "split", None) or cfg["defaults"].get("split", "parts")


def parse_selection(spec: str | None, n: int) -> list[int]:
    if not spec:
        return list(range(n))
    picked = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        picked.update(i - 1 for i in range(lo, hi + 1) if 1 <= i <= n)
    return sorted(picked)


def book_workdir(out_dir: Path, src: Path, title: str) -> Path:
    h = hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]
    d = out_dir / ".narrate-work" / f"{safe_name(title, 40)}-{h}"
    (d / "chunks").mkdir(parents=True, exist_ok=True)
    return d


def cmd_render(args, cfg, ctx):
    src = Path(args.input).expanduser()
    book = load_book(src, split_setting(args, cfg))
    picks = parse_selection(args.chapters, len(book.chapters))
    if not picks:
        raise Fatal("no chapters selected")
    chapters = [book.chapters[i] for i in picks]
    out_dir = Path(args.out or cfg["defaults"].get("output_dir", "~/Audiobooks")).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = book_workdir(out_dir, src, book.title)

    names = chain(cfg, args.profile)
    eng, rest = first_ready(cfg, ctx, names)
    while eng:
        prof = profile(cfg, eng.pname)
        if args.format:
            prof["format"] = args.format
        synth = Synth(eng, lazy_patcher(cfg, ctx, rest), args.no_wait, workdir)
        plans = [chunk_paras(ch.paras, eng.max_chars) for ch in chapters]
        total = sum(len(p) for p in plans)
        ident = eng.identity()
        cached = sum(1 for p in plans for c in p if chunk_path(workdir, ident, c.text).exists())
        emit("start", title=book.title, profile=eng.pname, engine=eng.name,
             total=total, cached=cached, chapters=len(chapters), workdir=str(workdir))
        try:
            plan, done = [], 0
            for ci, (ch, chunks) in enumerate(zip(chapters, plans)):
                entries = []
                for c in chunks:
                    path = chunk_path(workdir, ident, c.text)
                    share = gpu_share(prof) if eng.uses_gpu else None
                    if not path.exists():
                        t0 = time.monotonic()
                        write_atomic(path, synth(c.text))
                        if share is not None and share < 1:   # rest in proportion to the work
                            time.sleep((time.monotonic() - t0) * (1 - share) / share)
                    entries.append((path, c.pause))
                    done += 1
                    emit("progress", done=done, total=total, chapter=ci + 1, title=ch.title,
                         **({"gpu": round(share, 2)} if share is not None else {}))
                plan.append(entries)
            break
        except EngineUnavailable as e:
            if eng.successes or cached:
                raise Fatal(f"{eng.pname} stopped working partway ({e}); fix it and rerun to resume. "
                            "Not switching voices mid-book.")
            emit("fallback", profile=eng.pname, reason=str(e))
            log(f"[{eng.pname}] {e}; trying fallback")
            eng, rest = first_ready(cfg, ctx, rest)
    else:
        raise Fatal("no profile in the chain could run: " + " → ".join(names))

    out = assemble(book, chapters, plan, prof, eng.pname, out_dir, workdir)
    emit("done", output=str(out), profile=eng.pname)
    log(f"wrote {out}")


def cmd_outline(args, cfg, ctx):
    book = load_book(Path(args.input).expanduser(), split_setting(args, cfg))
    chs = []
    for i, ch in enumerate(book.chapters):
        first = next((p for p in ch.paras if p != SCENE and p != ch.title), "")
        entry = {"n": i + 1, "title": ch.title, "chars": ch.chars(),
                 "minutes": round(ch.chars() / CHARS_PER_MINUTE, 1), "preview": first[:200]}
        if args.text:
            entry["text"] = "\n\n".join("⁂" if p == SCENE else p for p in ch.paras)
        chs.append(entry)
    total = sum(c["chars"] for c in chs)
    data = {"title": book.title, "author": book.author, "file": str(Path(args.input).expanduser()),
            "chars": total, "minutes": round(total / CHARS_PER_MINUTE), "chapters": chs}
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return
    print(f"{book.title}{' — ' + book.author if book.author else ''}  (~{data['minutes']} min)")
    for c in chs:
        print(f"{c['n']:>4}  {c['minutes']:>6} min  {c['title'][:70]}")


def cmd_extract(args, cfg, ctx):
    book = load_book(Path(args.input).expanduser(), split_setting(args, cfg))
    out = [f"#+TITLE: {book.title}"] + ([f"#+AUTHOR: {book.author}"] if book.author else []) + [""]
    for ch in book.chapters:
        out.append(f"* {ch.title}")
        paras = ch.paras[1:] if ch.paras and ch.paras[0] == ch.title else ch.paras
        for p in paras:
            if p == SCENE:
                p = "⁂"
            elif re.match(r"^(\*|#|\||:\w+:\s*$)", p):
                p = " " + p    # keep org from reading it as markup
            out += ["", p]
        out.append("")
    dest = unique_path(Path(args.output).expanduser())
    dest.write_text("\n".join(out))
    emit("done", output=str(dest))
    log(f"wrote {dest}")


# Stock passages; `audition_text` and `tryout_text` in the profiles file replace them.
AUDITION_TEXT = (
    "The house was quiet in the early morning. Somewhere down the street a dog barked twice "
    "and stopped, and the kettle began, slowly, to sing.")
TRYOUT_TEXT = (
    "The letter arrived on a Tuesday, a day late. \"Is that the one?\" she asked, and he nodded "
    "without looking up. For a while neither of them said anything. Outside, the rain kept on, "
    "steady and unhurried, and the streetlights came on one by one.")


def cmd_audition(args, cfg, ctx):
    eng, rest = first_ready(cfg, ctx, chain(cfg, args.profile))
    if not eng:
        raise Fatal("no profile in the chain could run")
    prof = profile(cfg, eng.pname)
    text = Path(args.text_file).read_text() if args.text_file else \
        profile(cfg, args.profile).get("audition_text", AUDITION_TEXT)
    text = split_long(clean(text), eng.max_chars)[0]
    workdir = Path(tempfile.mkdtemp(prefix="narrate-audition-"))
    wav = Synth(eng, None, True, workdir)(text)
    raw = workdir / "raw.wav"
    write_atomic(raw, wav)
    out = Path(args.out).expanduser()
    run_ffmpeg(["-i", str(raw), "-af", audio_filter(prof), "-ar", str(RATE), "-ac", "1", str(out)])
    emit("done", output=str(out), profile=eng.pname)


def cmd_design(args, cfg, ctx):
    eng = make_engine(cfg, ctx, args.profile)
    if isinstance(eng, BreezeEngine):
        if args.refresh:
            raise Fatal("for a different take of a Breeze voice, change the profile's `seed`")
        out = Path(args.out).expanduser()
        try:
            if not eng.healthy():
                eng.start_server()
            eng.ensure_voice(preview=out)
        except (EngineUnavailable, Transient) as e:
            raise Fatal(str(e))
        emit("done", output=str(out), voice=eng.voice, profile=args.profile)
        return
    if not isinstance(eng, GeminiEngine) or not eng.design:
        raise Fatal(f"profile {args.profile!r} has no `design` description")
    if not eng.key:
        raise Fatal("no GEMINI_API_KEY")
    out = Path(args.out).expanduser()
    work = Path(tempfile.mkdtemp(prefix="narrate-design-"))
    preview = work / "preview.wav"
    vid = eng.designed_voice(refresh=args.refresh, preview=preview)
    if not vid:
        raise Fatal("voice design failed (see log)")
    if not preview.exists():   # cached voice, or no sample returned: read a line with it
        eng.voice_id = vid
        write_atomic(preview, Synth(eng, None, True, work)(
            "This is the voice you designed, reading a short passage so you can judge it."))
    shutil.move(str(preview), str(out))
    emit("done", output=str(out), voice=vid, profile=args.profile)


def read_designs(path: str | None, prof: dict) -> list[tuple[str, str | None]]:
    """One description per line, optionally followed by ` | style`; # starts a comment."""
    if not path:
        return [((prof.get("design") or "").strip(), None)]
    out = []
    for line in Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        design, _, style = line.partition(" | ")
        out.append((design.strip(), style.strip() or None))
    return out


def cmd_tryout(args, cfg, ctx):
    """Several designed voices, several takes each, all reading the same
    passage at the profile's tempo and filter, plus one file of them all."""
    pname = args.profile
    eng = make_engine(cfg, ctx, pname)
    prof = profile(cfg, pname)
    if args.tempo:
        prof["tempo"] = args.tempo
    if not isinstance(eng, (GeminiEngine, BreezeEngine)):
        raise Fatal(f"tryout works with gemini and breeze profiles; {pname!r} uses {eng.name}")
    designs = read_designs(args.designs, prof)
    if not designs or not all(d for d, _ in designs):
        raise Fatal("no designs to try: give a --designs file or a profile with `design`")
    text = Path(args.text_file).read_text() if args.text_file else prof.get("tryout_text", TRYOUT_TEXT)
    text = split_long(clean(text), eng.max_chars)[0]
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="narrate-tryout-"))
    base_style = eng.style

    if isinstance(eng, GeminiEngine):
        if not eng.key:
            raise Fatal("no GEMINI_API_KEY")
    else:
        try:
            if not eng.healthy():
                eng.start_server()
        except (EngineUnavailable, Transient) as e:
            raise Fatal(str(e))

    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    total = len(designs) * args.takes
    emit("start", title=f"tryout {pname}", total=total, cached=0, chapters=len(designs), profile=pname)
    results = []

    def write_index():
        gap = 1.5
        parts, t = [], 0.0
        for r in results:
            r["start"] = t
            data = (out / r["file"]).read_bytes()
            parts.append(data)
            t += wav_seconds(data) + gap
        if parts:
            write_atomic(out / "all.wav", join_wavs(parts, gap))
        manifest = {"profile": pname, "engine": eng.name, "created": stamp,
                    "tempo": args.tempo, "takes": [{k: v for k, v in r.items() if k not in ("paste", "start")}
                                                   for r in results]}
        write_atomic(out / "tryout.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode())
        nd, nt = len(designs), args.takes
        source = ("the profile's own description" if not args.designs
                  else f"from {Path(args.designs).name}")
        lines = [f"#+TITLE: {eng.name.capitalize()} voice tryout: "
                 f"{nd} description{'s' * (nd != 1)}, {nt} take{'s' * (nt != 1)} each",
                 f"#+SUBTITLE: profile {pname}, {source}, {stamp}", "",
                 f"Every take reads the same passage at tempo {float(prof.get('tempo', 1.0)):g}"
                 + (" through the profile's filter" if prof.get("filter") else "") + ". "
                 f"=all.wav= plays them in order, {gap:g} s apart; the first column of the table "
                 "is where each one starts. Take 02-3 is description 2, third take.", "",
                 "To keep a take, put the cursor on its row and run =M-x narrate-keep=. It asks for a "
                 "name, adds a profile by that name to profiles.toml, and plays the kept voice.", "",
                 "* Descriptions", ""]
        for i, (d_, st) in enumerate(designs, 1):
            lines.append(f"{i}. {d_}" + (f" /(style: {st})/" if st else ""))
        lines += ["", "* Takes", "", "| at | take | description | voice |", "|-"]
        for r in results:
            m, sec = divmod(int(r["start"]), 60)
            lines.append(f"| {m}:{sec:02d} | [[file:{r['file']}][{r['file']}]] | {int(r['file'][:2])} "
                         f"| {r['paste']} |")
        (out / "index.org").write_text("\n".join(lines) + "\n")

    for di, (design, style) in enumerate(designs, 1):
        eng.style = style if style is not None else base_style
        for take in range(1, args.takes + 1):
            name = f"{di:02d}-{take}.wav"
            try:
                if isinstance(eng, GeminiEngine):
                    eng.voice_id = eng.new_voice(design)
                    paste = f'voice_id = "{eng.voice_id}"'
                    entry = {"voice_id": eng.voice_id}
                else:
                    eng.seed = int(prof.get("seed", 42)) + take - 1
                    eng.voice, ref = eng.trial_voice(design, eng.seed)
                    paste = f"seed = {eng.seed}"
                    ref_name = name.replace(".wav", ".ref.wav")
                    write_atomic(out / ref_name, ref)
                    entry = {"seed": eng.seed, "ref": ref_name, "ref_line": eng.ref_line}
                wav = Synth(eng, None, True, work)(text)
            except (EngineUnavailable, Transient, Refused) as e:
                log(f"[{pname}] design {di}, take {take} failed: {e}")
                if isinstance(e, EngineUnavailable) and not results:
                    raise Fatal(str(e))
                continue
            raw = work / "raw.wav"
            write_atomic(raw, wav)
            run_ffmpeg(["-i", str(raw), "-af", audio_filter(prof), "-ar", str(RATE), "-ac", "1",
                        "-c:a", "pcm_s16le", str(out / name)])
            results.append({"file": name, "design": design, "style": style, "paste": paste, **entry})
            log(f"[{pname}] {name}  {paste}")
            emit("progress", done=len(results), total=total)
            write_index()
    if isinstance(eng, BreezeEngine):
        eng.stop()
    if not results:
        raise Fatal("no takes came back")
    emit("done", output=str(out / "index.org"))


def load_tryout(d: Path) -> dict:
    """A tryout's manifest; older tryouts only have index.org, so read that."""
    if (d / "tryout.json").exists():
        return json.loads((d / "tryout.json").read_text())
    idx = d / "index.org"
    if not idx.exists():
        raise Fatal(f"{d} isn't a tryout folder (no tryout.json or index.org)")
    text = idx.read_text()
    m = re.search(r"^#\+TITLE: (\S+) tryout", text, re.M)
    takes, engine = [], "breeze"
    for row in re.finditer(r"^\|[^|]*\|\s*\[\[file:([^\]]+)\]\[[^\]]*\]\]\s*\|(.*?)\|(.*?)\|\s*$", text, re.M):
        file, voice, design = row.group(1), row.group(2), row.group(3).strip()
        style = re.search(r' ⟨style: (.*)⟩$', design)
        t = {"file": file, "design": re.sub(r" ⟨style: .*⟩$", "", design),
             "style": style.group(1) if style else None}
        if (v := re.search(r'voice_id = "([^"]+)"', voice)):
            t["voice_id"], engine = v.group(1), "gemini"
        if (v := re.search(r"seed = (\d+)", voice)):
            t["seed"] = int(v.group(1))
        takes.append(t)
    if not m or not takes:
        raise Fatal(f"couldn't read the takes in {idx}")
    return {"profile": m.group(1), "engine": engine, "takes": takes, "tempo": None}


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(v, ensure_ascii=False)   # JSON strings and string lists are valid TOML


def cmd_keep(args, cfg, ctx):
    """Turn one tryout take into a profile of its own, appended to profiles.toml."""
    name = args.name
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise Fatal("a profile name can use letters, digits, dash, and underscore")
    if name in cfg["profiles"]:
        raise Fatal(f"there's already a profile named {name!r}; pick another name")
    d = Path(args.dir).expanduser()
    tr = load_tryout(d)
    want = Path(args.take).stem.replace(".ref", "")
    take = next((t for t in tr["takes"] if Path(t["file"]).stem == want), None)
    if not take:
        raise Fatal(f"no take {args.take!r} in {d.name} "
                    f"(takes: {', '.join(Path(t['file']).stem for t in tr['takes'])})")
    base = tr["profile"]
    base_prof = cfg["profiles"].get(base, {})
    config_path = Path(ctx["config_path"])
    stem = Path(take["file"]).stem
    keys: dict = {}
    notes = [f"# kept from the {base} tryout in {d.name}, take {stem}",
             f"# designed from: {take['design']}"]

    if tr["engine"] == "breeze":
        merged = {**DEFAULTS, **cfg["defaults"], **base_prof, "engine": "breeze"}
        ref_line = merged.get("ref_line") or BreezeEngine.REF_LINE
        voices_dir = config_path.with_name("breeze-voices")
        voices_dir.mkdir(parents=True, exist_ok=True)
        dest = voices_dir / f"{name}.ref.wav"
        if dest.exists():
            raise Fatal(f"{dest} already exists; pick another name")
        src = d / take["ref"] if take.get("ref") else None
        if src and src.exists():
            write_atomic(dest, src.read_bytes())
        else:
            # tryouts made before reference clips were saved: remake it from the seed
            eng = BreezeEngine(base, merged, ctx)
            try:
                if not eng.healthy():
                    eng.start_server()
                write_atomic(dest, eng.reference_clip(take["design"], take["seed"]))
            except (EngineUnavailable, Transient, Refused) as e:
                raise Fatal(str(e))
            finally:
                eng.stop()
            log(f"[{name}] this tryout predates saved reference clips, so the voice was remade "
                f"from seed {take['seed']}; listen to the preview to check it's the one you heard")
        keys.update(engine="breeze", ref_audio=f"breeze-voices/{dest.name}",
                    ref_text=take.get("ref_line") or ref_line, seed=take["seed"])
        carry = ("model", "server", "port", "device", "cfg_scale")
        preview = dest
    else:
        keys.update(engine="gemini", model=base_prof.get("model", GeminiEngine.DEFAULT_MODEL))
        if base_prof.get("voice"):
            keys["voice"] = base_prof["voice"]
        keys["voice_id"] = take["voice_id"]
        carry = ("gender", "language", "min_interval", "max_chars")
        preview = d / take["file"]
    for k in carry:
        if k in base_prof:
            keys[k] = base_prof[k]
    style = take.get("style") if take.get("style") is not None else base_prof.get("style")
    if style:
        keys["style"] = style
    for k in ("tempo", "filter", "fallback"):
        if k in base_prof:
            keys[k] = base_prof[k]
    if tr.get("tempo"):
        keys["tempo"] = tr["tempo"]

    block = "\n".join([f"[profiles.{name}]", *notes] + [f"{k} = {toml_value(v)}" for k, v in keys.items()])
    old = config_path.read_text()
    new = old.rstrip("\n") + "\n\n\n" + block + "\n"
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as e:
        raise Fatal(f"the new profile wouldn't parse, so profiles.toml is unchanged: {e}")
    write_atomic(config_path, new.encode())
    log(f"[{name}] added to {config_path}")
    emit("done", output=str(preview), profile=name)


def cmd_profiles(args, cfg, ctx):
    rows = []
    for name, p in cfg["profiles"].items():
        fb = p.get("fallback", [])
        cls = ENGINES.get(p.get("engine", ""))
        rows.append({"name": name, "engine": p.get("engine", ""), "voice": p.get("voice", ""),
                     "design": p.get("design", ""), "style": p.get("style", ""),
                     "fallback": [fb] if isinstance(fb, str) else fb, "note": p.get("note", ""),
                     "remote": getattr(cls, "remote", None), "uses_gpu": getattr(cls, "uses_gpu", False)})
    if args.json:
        print(json.dumps({"default": cfg["defaults"].get("profile"),
                          "split": cfg["defaults"].get("split", "parts"),
                          "output_dir": str(Path(cfg["defaults"].get("output_dir", "~/Audiobooks")).expanduser()),
                          "profiles": rows}, ensure_ascii=False))
        return
    for r in rows:
        print(f"{r['name']:<18} {r['engine']:<10} {r['voice']:<22} → {', '.join(r['fallback']) or '-'}")
