"""Pipeline steps shared by the CLI and the GUI.

Each step takes a project (and options), mutates/saves it, and reports
progress through an optional ``log(message)`` callback.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from autokaraoke import config
from autokaraoke.audio import probe_duration, require_ffmpeg, to_wav
from autokaraoke.project import Project

Log = Callable[[str], None]


def _noop(msg: str) -> None:
    pass


def is_url(source: str) -> bool:
    return re.match(r"^https?://", source) is not None


def create_project(
    source: str,
    out: Path = config.PROJECTS_DIR,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    lang: str | None = None,
    log: Log = _noop,
) -> Project:
    """Create a project folder from an audio file or a URL (if downloads are enabled)."""
    require_ffmpeg()
    staging = out / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    proj = Project(source=source)
    if is_url(source):
        from autokaraoke.sources.youtube import download_audio

        log(f"Downloading {source}")
        src_file, info = download_audio(source, staging)
        proj.title, proj.artist, proj.album = info.title, info.artist, info.album
    else:
        src_file = Path(source)
        if not src_file.exists():
            raise FileNotFoundError(source)
        stem = src_file.stem
        if " - " in stem:
            proj.artist, proj.title = (s.strip() for s in stem.split(" - ", 1))
        else:
            proj.title = stem

    proj.title = title or proj.title
    proj.artist = artist or proj.artist
    proj.album = album or proj.album
    proj.language = lang or ""

    project_dir = out / proj.slug
    project_dir.mkdir(parents=True, exist_ok=True)
    proj.path = project_dir
    log("Converting audio")
    to_wav(src_file, project_dir / "full.wav")
    shutil.rmtree(staging, ignore_errors=True)
    proj.audio_full = "full.wav"
    proj.duration = probe_duration(project_dir / "full.wav")
    proj.save()
    log(f"Project created in {project_dir}")
    return proj


class LyricsNotFound(LookupError):
    pass


def fetch_lyrics(proj: Project, lrc_file: Path | None = None, log: Log = _noop) -> None:
    """Load lyrics from LRCLIB (synced if available) or a local file and syllabify them."""
    from autokaraoke.sources.lrclib import fetch_lyrics as lrclib_fetch
    from autokaraoke.sources.lrclib import parse_lrc
    from autokaraoke.text.lyrics import lines_from_lrc, lines_from_plain
    from autokaraoke.text.syllabify import detect_language

    if lrc_file:
        text = Path(lrc_file).read_text(encoding="utf-8")
        synced, plain, source = (text, "", "file-synced") if parse_lrc(text) else ("", text, "file-plain")
    else:
        log(f"Searching LRCLIB for {proj.artist} - {proj.title}")
        res = lrclib_fetch(proj.title, proj.artist, proj.album, proj.duration)
        if res is None:
            raise LyricsNotFound(f"No lyrics found on LRCLIB for {proj.artist} - {proj.title}")
        if res.instrumental:
            log("LRCLIB marks this track as instrumental")
        synced, plain = res.synced, res.plain
        source = "lrclib-synced" if synced else "lrclib-plain"
        log(f"Found: {res.artist} - {res.track} ({res.duration:.0f}s), "
            f"{'synced' if synced else 'plain'} lyrics")

    text_for_lang = plain or "\n".join(t for _, t in parse_lrc(synced))
    if not proj.language:
        proj.language = detect_language(text_for_lang)
        log(f"Detected language: {proj.language or 'unknown'}")
    proj.lines = (
        lines_from_lrc(synced, proj.language, proj.duration)
        if synced
        else lines_from_plain(plain, proj.language)
    )
    proj.lyrics_source = source
    proj.save()
    n_syl = sum(len(ln.syllables()) for ln in proj.lines)
    log(f"{len(proj.lines)} lines, {n_syl} syllables")


def separate(proj: Project, model: str = config.SEPARATION_MODEL, log: Log = _noop) -> None:
    from autokaraoke.separation import separate as run_separation

    assert proj.path is not None
    log(f"Separating vocals with {model} (the first run downloads the model)")
    vocals, instrumental = run_separation(proj.path / proj.audio_full, proj.path, model)
    proj.audio_vocals = vocals.name
    proj.audio_instrumental = instrumental.name
    proj.save()
    log("Stems ready")


_aligner_cache: dict[tuple[str, str], object] = {}


def _get_aligner(engine: str, language: str):
    """Aligners are expensive to load; keep them for the session (GUI re-runs)."""
    from autokaraoke.align.pipeline import make_aligner

    # Qwen ignores the language; CTC uses it for number spelling.
    key = (engine, language if engine == "mms" else "")
    if key not in _aligner_cache:
        _aligner_cache[key] = make_aligner(engine, language)
    return _aligner_cache[key]


TRANSCRIPT_FILE = "transcript.json"
_transcriber = None


def transcribe(proj: Project, force: bool = False, log: Log = _noop):
    """ASR of the vocal stem with word times, cached in the project folder."""
    from autokaraoke.align.base import load_mono
    from autokaraoke.asr import Transcriber, Transcript

    global _transcriber
    assert proj.path is not None
    cache = proj.path / TRANSCRIPT_FILE
    if cache.exists() and not force:
        return Transcript.load(cache)
    audio = proj.abspath(proj.audio_vocals) or proj.abspath(proj.audio_full)
    if not proj.audio_vocals:
        log("No vocal stem: transcribing the full mix (separate first for better results)")
    if _transcriber is None:
        log("Loading speech recognition model (the first run downloads ~4.5 GB)")
        _transcriber = Transcriber()
    log("Transcribing vocals")
    tr = _transcriber.transcribe(load_mono(audio), proj.language)
    tr.save(cache)
    log(f"Transcript: {len(tr.words)} words ({tr.language or 'language unknown'})")
    return tr


def reconcile(proj: Project, log: Log = _noop):
    """Ask the LLM how the lyrics differ from what is sung. Returns a Proposal to review."""
    from autokaraoke.text.reconcile import propose

    tr = transcribe(proj, log=log)
    log(f"Comparing lyrics and transcript with {config.OPENROUTER_MODEL}")
    proposal = propose(proj, tr)
    cost = f", cost ${proposal.cost:.4f}" if proposal.cost is not None else ""
    log(f"{len(proposal.changes)} suggested changes{cost}")
    return proposal


def apply_proposal(proj: Project, proposal) -> None:
    """Replace the lyrics; new or edited lines get a rough even timing until the next Auto-sync."""
    from autokaraoke.align.naive import time_line

    proj.lines = proposal.result()
    for ln in proj.lines:
        if any(s.start is None for s in ln.syllables()):
            time_line(ln)
    if "+llm" not in proj.lyrics_source:
        proj.lyrics_source = (proj.lyrics_source or "asr") + "+llm"
    proj.save()


def lyrics_from_audio(proj: Project, log: Log = _noop) -> None:
    """No lyrics anywhere: write them from the transcript (cleaned up by the LLM if available)."""
    from autokaraoke.text.reconcile import lines_from_transcript

    tr = transcribe(proj, log=log)
    if not proj.language and tr.language:
        from autokaraoke.asr import LANGUAGES

        proj.language = next((k for k, v in LANGUAGES.items() if v == tr.language), "")
    if len(tr.words) < 5:
        raise LyricsNotFound("No lyrics could be recognised in the vocals")
    proj.lines = []
    proj.lyrics_source = "asr"
    if config.llm_available():
        from autokaraoke.llm import LlmError

        try:
            apply_proposal(proj, reconcile(proj, log))
        except (LlmError, ValueError) as e:
            log(f"LLM clean-up failed ({e}); using the raw transcript")
    else:
        log("Using the raw transcript as lyrics; fix them in the editor")
    if not proj.lines:
        proj.lines = lines_from_transcript(tr, proj.language)
        proj.save()
    log(f"{len(proj.lines)} lines from the recording; check them in the editor")


def place_lines(proj: Project, log: Log = _noop) -> None:
    """Give unsynced lyric lines a start time by finding them in the transcript."""
    from autokaraoke.text.anchors import fill_starts, match_line_starts

    tr = transcribe(proj, log=log)
    starts = match_line_starts([ln.text for ln in proj.lines], tr.words)
    found = sum(s is not None for s in starts)
    hints = [ln.start for ln in proj.lines]
    filled = fill_starts(starts, proj.duration, hints)
    for k, ln in enumerate(proj.lines):
        ln.start = filled[k]
        ln.end = filled[k + 1] if k + 1 < len(filled) else proj.duration
    log(f"Located {found}/{len(proj.lines)} lines in the transcript")


def align(proj: Project, engine: str = "qwen", cross_check: bool = True, log: Log = _noop) -> int:
    """Align syllables; return the number of syllables flagged for review."""
    if engine != "naive" and any(ln.start is None for ln in proj.lines):
        try:
            place_lines(proj, log)
        except ImportError:
            log("Speech recognition not installed: locating lines with a whole-song pass")
    if engine == "naive":
        from autokaraoke.align.naive import time_lines

        if any(ln.start is None for ln in proj.lines):
            raise ValueError("Naive timing needs synced (LRC) lyrics")
        time_lines(proj.lines)
    else:
        from autokaraoke.align.pipeline import align_project

        if not proj.audio_vocals:
            log("No vocal stem: aligning on the full mix (separate first for better results)")
        log(f"Loading aligner {engine}")
        aligner = _get_aligner(engine, proj.language)
        secondary = _get_aligner("mms", proj.language) if engine == "qwen" and cross_check else None
        align_project(proj, aligner, secondary, progress=lambda f, msg: log(msg))
    proj.save()
    flagged = sum(1 for ln in proj.lines for s in ln.syllables() if s.confidence < 0.5)
    log(f"Aligned with {engine}; {flagged} syllables flagged for review")
    return flagged


def render(proj: Project, output: Path | None = None, background: Path | None = None,
           log: Log = _noop, background_color: str | None = None, style=None) -> Path:
    """``style`` (export.ass.AssStyle) defaults to the project's saved style."""
    from autokaraoke.render import render as run_render

    assert proj.path is not None
    out = output or proj.path / f"{proj.slug}.mkv"
    if any(ln.backing for ln in proj.lines):
        log("Mixing backing vocals into the karaoke track")
    log("Rendering video")
    run_render(proj, out, background=background, background_color=background_color, style=style)
    log(f"Rendered {out}")
    return out
