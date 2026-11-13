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

        log(f'正在下载 {source}')
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
    log('正在转换音频')
    to_wav(src_file, project_dir / "full.wav")
    shutil.rmtree(staging, ignore_errors=True)
    proj.audio_full = "full.wav"
    proj.duration = probe_duration(project_dir / "full.wav")
    proj.save()
    log(f'项目已创建于：{project_dir}')
    return proj


class LyricsNotFound(LookupError):
    pass


def fetch_lyrics(proj: Project, lrc_file: Path | None = None, log: Log = _noop) -> None:
    """Load lyrics from LRCLIB (synced if available) or a local file and syllabify them."""
    from autokaraoke.sources.lrclib import fetch_lyrics as lrclib_fetch
    from autokaraoke.sources.lrclib import parse_lrc
    from autokaraoke.text.lyrics import lines_from_lrc, lines_from_plain
    from autokaraoke.text.syllabify import detect_language
    from autokaraoke.text.chinese import script_language, to_simplified

    if lrc_file:
        text = Path(lrc_file).read_text(encoding="utf-8")
        synced, plain, source = (text, "", "file-synced") if parse_lrc(text) else ("", text, "file-plain")
    else:
        log(f'正在 LRCLIB 搜索：{proj.artist} - {proj.title}')
        res = lrclib_fetch(proj.title, proj.artist, proj.album, proj.duration)
        if res is None:
            raise LyricsNotFound(f'在 LRCLIB 中未找到歌词：{proj.artist} - {proj.title}')
        if res.instrumental:
            log('LRCLIB 将该歌曲标记为纯音乐')
        synced, plain = res.synced, res.plain
        source = "lrclib-synced" if synced else "lrclib-plain"
        log(f"已找到：{res.artist} - {res.track} ({res.duration:.0f}s), {('synced' if synced else 'plain')}歌词")

    text_for_lang = plain or "\n".join(t for _, t in parse_lrc(synced))
    hint = script_language(text_for_lang)
    if not proj.language or (hint == "zh" and proj.language in ("ko", "zh-cn", "zh-tw")):
        proj.language = detect_language(text_for_lang)
        log(f"检测到语言：{proj.language or 'unknown'}")
    if config.simplified_chinese_lyrics() and hint == "zh":
        converted_synced, converted_plain = to_simplified(synced), to_simplified(plain)
        if (converted_synced, converted_plain) != (synced, plain):
            log("已将获取的中文歌词转换为简体，保留原有时间标签。")
        synced, plain = converted_synced, converted_plain
    proj.lines = (
        lines_from_lrc(synced, proj.language, proj.duration)
        if synced
        else lines_from_plain(plain, proj.language)
    )
    proj.lyrics_source = source
    proj.save()
    n_syl = sum(len(ln.syllables()) for ln in proj.lines)
    log(f'{len(proj.lines)} 行歌词，{n_syl} 个音节')


def separate(proj: Project, model: str = config.SEPARATION_MODEL, log: Log = _noop) -> None:
    from autokaraoke.separation import separate as run_separation

    assert proj.path is not None
    log(f'正在使用以下模型分离人声：{model}（首次使用将下载模型）')
    vocals, instrumental = run_separation(proj.path / proj.audio_full, proj.path, model, log=log)
    proj.audio_vocals = vocals.name
    proj.audio_instrumental = instrumental.name
    proj.save()
    log('人声和伴奏已就绪')


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
    from autokaraoke.asr import LANGUAGES, Transcriber, Transcript
    from autokaraoke.text.chinese import script_language

    global _transcriber
    assert proj.path is not None
    if script_language("\n".join(line.text for line in proj.lines)) == "zh":
        proj.language = "zh"
    cache = proj.path / TRANSCRIPT_FILE
    if cache.exists() and not force:
        cached = Transcript.load(cache)
        expected = LANGUAGES.get(proj.language)
        if not expected or cached.language.casefold() == expected.casefold():
            return cached
        log("歌词语言已修正，将重新识别人声，不复用其他语言的转写缓存。")
    audio = proj.abspath(proj.audio_vocals) or proj.abspath(proj.audio_full)
    if not proj.audio_vocals:
        log('尚未分离人声，将直接识别原曲（建议先分离人声以提高效果）')
    if _transcriber is None:
        log('正在加载语音识别模型（首次使用将下载约 4.5 GB）')
        _transcriber = Transcriber()
    log('正在识别人声歌词')
    tr = _transcriber.transcribe(load_mono(audio), proj.language)
    tr.save(cache)
    log(f"识别结果：{len(tr.words)} 个词（{tr.language or '未知语言'})")
    return tr


def reconcile(proj: Project, log: Log = _noop):
    """Ask the LLM how the lyrics differ from what is sung. Returns a Proposal to review."""
    from autokaraoke.text.reconcile import propose

    tr = transcribe(proj, log=log)
    log(f'正在使用以下模型比对歌词与识别文本：{config.OPENROUTER_MODEL}')
    proposal = propose(proj, tr)
    cost = f'，费用 ${proposal.cost:.4f}' if proposal.cost is not None else ""
    log(f'{len(proposal.changes)} 条修改建议{cost}')
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
        raise LyricsNotFound('未能从人声中识别出歌词')
    proj.lines = []
    proj.lyrics_source = "asr"
    if config.llm_available():
        from autokaraoke.llm import LlmError

        try:
            apply_proposal(proj, reconcile(proj, log))
        except (LlmError, ValueError) as e:
            log(f'大语言模型整理失败（{e}）；将使用原始识别文本')
    else:
        log('正在使用原始识别文本作为歌词，请在编辑器中核对')
    if not proj.lines:
        proj.lines = lines_from_transcript(tr, proj.language)
        proj.save()
    log(f'{len(proj.lines)} 行歌词，请在编辑器中核对')


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
    log(f'已定位 {found}/{len(proj.lines)} 行歌词')


def align(proj: Project, engine: str = "qwen", cross_check: bool = True, log: Log = _noop) -> int:
    """Align syllables; return the number of syllables flagged for review."""
    if engine != "naive" and any(ln.start is None for ln in proj.lines):
        try:
            place_lines(proj, log)
        except ImportError:
            log('尚未安装语音识别组件，将通过全曲对齐定位歌词行')
    if engine == "naive":
        from autokaraoke.align.naive import time_lines

        if any(ln.start is None for ln in proj.lines):
            raise ValueError('均匀分配模式需要带时间信息的 LRC 歌词')
        time_lines(proj.lines)
    else:
        from autokaraoke.align.pipeline import align_project

        if not proj.audio_vocals:
            log('尚未分离人声，将直接对齐原曲（建议先分离人声以提高效果）')
        log(f'正在加载对齐模型：{engine}')
        aligner = _get_aligner(engine, proj.language)
        secondary = _get_aligner("mms", proj.language) if engine == "qwen" and cross_check else None
        align_project(proj, aligner, secondary, progress=lambda f, msg: log(msg))
    proj.save()
    flagged = sum(1 for ln in proj.lines for s in ln.syllables() if s.confidence < 0.5)
    log(f'已完成对齐，使用模型：{engine}; {flagged} 个音节标记为待复核')
    return flagged


def render(proj: Project, output: Path | None = None, background: Path | None = None,
           log: Log = _noop, background_color: str | None = None, style=None) -> Path:
    """``style`` (export.ass.AssStyle) defaults to the project's saved style."""
    from autokaraoke.render import render as run_render

    assert proj.path is not None
    out = output or proj.path / f"{proj.slug}.mkv"
    if any(ln.backing for ln in proj.lines):
        log('正在将和声混入卡拉 OK 音轨')
    log('正在生成视频')
    run_render(proj, out, background=background, background_color=background_color, style=style)
    log(f'视频已生成：{out}')
    return out
