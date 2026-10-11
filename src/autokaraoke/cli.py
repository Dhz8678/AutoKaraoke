"""Command line interface: each pipeline step is a subcommand, ``run`` chains them all."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from autokaraoke import config, steps
from autokaraoke.project import Project

app = typer.Typer(no_args_is_help=True, help="Semi-automatic karaoke track generator.")
console = Console()

ProjectArg = Annotated[Path, typer.Argument(help="Project directory or project.akp.json")]
EngineOpt = Annotated[
    str,
    typer.Option(help="qwen (Qwen3-ForcedAligner), mms (CTC, ~1100 languages) "
                      "or naive (even spread over LRC lines)"),
]
CrossCheckOpt = Annotated[
    bool, typer.Option(help="With qwen: also run mms to flag doubtful syllables and as fallback")
]


def log(msg: str) -> None:
    console.print(f"  {msg}")


@app.command()
def new(
    source: Annotated[str, typer.Argument(help="Audio file, or a YouTube/YT Music URL")],
    title: Annotated[Optional[str], typer.Option(help="Song title (guessed if omitted)")] = None,
    artist: Annotated[Optional[str], typer.Option()] = None,
    album: Annotated[Optional[str], typer.Option()] = None,
    lang: Annotated[Optional[str], typer.Option(help="Lyrics language code, e.g. it, en")] = None,
    out: Annotated[Path, typer.Option(help="Parent folder for projects")] = config.PROJECTS_DIR,
    no_lyrics: Annotated[bool, typer.Option(help="Skip the LRCLIB lookup")] = False,
) -> Path:
    """Create a project from an audio file or URL and fetch its lyrics."""
    proj = steps.create_project(source, out, title, artist, album, lang, log=log)
    if not no_lyrics:
        try:
            steps.fetch_lyrics(proj, log=log)
        except steps.LyricsNotFound as e:
            console.print(f"[red]{e}[/] Provide them with 'lyrics --lrc-file'.")
    assert proj.path is not None
    return proj.path


@app.command()
def lyrics(
    project: ProjectArg,
    lrc_file: Annotated[Optional[Path], typer.Option(help="Use a local .lrc/.txt instead of LRCLIB")] = None,
    from_audio: Annotated[bool, typer.Option(help="Transcribe the lyrics from the vocals")] = False,
) -> None:
    """Fetch lyrics from LRCLIB (synced if available) and split them into syllables."""
    proj = Project.load(project)
    if from_audio:
        steps.lyrics_from_audio(proj, log=log)
        return
    try:
        steps.fetch_lyrics(proj, lrc_file, log=log)
    except steps.LyricsNotFound as e:
        console.print(f"[red]{e}[/] Provide them with --lrc-file, or use --from-audio.")
        raise typer.Exit(1)


@app.command()
def transcribe(
    project: ProjectArg,
    force: Annotated[bool, typer.Option(help="Ignore the cached transcript")] = False,
) -> None:
    """Speech recognition of the vocals, with word times (saved as transcript.json)."""
    tr = steps.transcribe(Project.load(project), force=force, log=log)
    for start, _end, text in tr.phrases():
        console.print(f"[dim]{start:7.2f}[/] {text}")


def _print_changes(proposal) -> None:
    style = {"edit": "yellow", "add": "green", "remove": "red", "doubt": "cyan"}
    for i, c in enumerate(proposal.changes):
        t = f"{c.start:7.2f}" if c.start is not None else "      ?"
        if c.kind == "edit":
            body = f"{c.old}  →  {c.new}"
        elif c.kind == "doubt":
            body = f"{c.old}  [dim](heard: {c.new})[/]"
        else:
            body = c.new if c.kind == "add" else c.old
        console.print(f"{i:3d} [dim]{t}[/] [{style[c.kind]}]{c.kind:6}[/] {body}")
    for n in proposal.notes:
        console.print(f"    [dim]• {n}[/]")


@app.command()
def reconcile(
    project: ProjectArg,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Apply without asking")] = False,
    accept_doubts: Annotated[bool, typer.Option(help="Also use the transcript text for doubtful lines")] = False,
) -> None:
    """[Experimental] Fix the lyrics to match what is sung (transcript + LLM via OpenRouter).

    Expands repeated choruses, removes section labels and fixes wrong words. Run
    'align' again afterwards. Needs AUTOKARAOKE_EXPERIMENTAL_LLM=1 and OPENROUTER_API_KEY.
    """
    if not config.llm_available():
        console.print("[red]Experimental LLM features are off.[/] Set AUTOKARAOKE_EXPERIMENTAL_LLM=1 "
                      "and OPENROUTER_API_KEY (environment or .env).")
        raise typer.Exit(1)
    proj = Project.load(project)
    proposal = steps.reconcile(proj, log=log)
    for c in proposal.changes:
        c.accepted = c.kind != "doubt" or accept_doubts
    if not proposal.changes:
        console.print("The lyrics already match the recording.")
        return
    _print_changes(proposal)
    if yes or typer.confirm("Apply these changes?"):
        steps.apply_proposal(proj, proposal)
        console.print(f"Applied. {len(proj.lines)} lines; run 'align' again.")


@app.command()
def separate(
    project: ProjectArg,
    model: Annotated[str, typer.Option(help="audio-separator model filename")] = config.SEPARATION_MODEL,
) -> None:
    """Split the song into vocals and instrumental stems."""
    steps.separate(Project.load(project), model, log=log)


@app.command()
def align(project: ProjectArg, engine: EngineOpt = "qwen", cross_check: CrossCheckOpt = True) -> None:
    """Compute syllable timings."""
    steps.align(Project.load(project), engine, cross_check, log=log)


@app.command()
def export(
    project: ProjectArg,
    fmt: Annotated[str, typer.Option("--format", help="ass or lrc")] = "ass",
    output: Annotated[Optional[Path], typer.Option()] = None,
) -> None:
    """Export timed lyrics as ASS subtitles or enhanced LRC."""
    from autokaraoke.export.ass import write_ass
    from autokaraoke.export.lrc import write_lrc

    proj = Project.load(project)
    assert proj.path is not None
    out = output or proj.path / f"{proj.slug}.{fmt}"
    {"ass": write_ass, "lrc": write_lrc}[fmt](proj, out)
    console.print(f"Wrote [green]{out}[/]")


@app.command()
def render(
    project: ProjectArg,
    output: Annotated[Optional[Path], typer.Option(help="Output video (.mkv or .mp4)")] = None,
    background: Annotated[Optional[Path], typer.Option(help="Background image")] = None,
    background_color: Annotated[str, typer.Option(help="Background colour without an image, e.g. 0x101018 or navy")] = "0x101018",
) -> None:
    """Render the karaoke video with instrumental, original and vocals audio tracks."""
    out = steps.render(Project.load(project), output, background, log=log, background_color=background_color)
    console.print(f"[green]Done:[/] {out}")


@app.command()
def run(
    source: Annotated[str, typer.Argument(help="Audio file, or a YouTube/YT Music URL")],
    title: Annotated[Optional[str], typer.Option()] = None,
    artist: Annotated[Optional[str], typer.Option()] = None,
    album: Annotated[Optional[str], typer.Option()] = None,
    lang: Annotated[Optional[str], typer.Option()] = None,
    out: Annotated[Path, typer.Option()] = config.PROJECTS_DIR,
    engine: EngineOpt = "qwen",
    cross_check: CrossCheckOpt = True,
    skip_separation: Annotated[bool, typer.Option()] = False,
    background: Annotated[Optional[Path], typer.Option()] = None,
    reconcile_lyrics: Annotated[bool, typer.Option(
        "--reconcile/--no-reconcile",
        help="[Experimental] Fix the lyrics with transcript + LLM (needs "
             "AUTOKARAOKE_EXPERIMENTAL_LLM=1 and an OpenRouter key)")] = False,
) -> None:
    """Full pipeline: new -> separate -> (reconcile) -> align -> render."""
    proj = steps.create_project(source, out, title, artist, album, lang, log=log)
    try:
        steps.fetch_lyrics(proj, log=log)
        found = True
    except steps.LyricsNotFound as e:
        console.print(f"[yellow]{e}[/]: the lyrics will be transcribed from the vocals")
        found = False
    if not skip_separation:
        steps.separate(proj, log=log)
    if not found:
        steps.lyrics_from_audio(proj, log=log)
    elif reconcile_lyrics and config.llm_available():
        proposal = steps.reconcile(proj, log=log)
        _print_changes(proposal)
        steps.apply_proposal(proj, proposal)
    steps.align(proj, engine, cross_check, log=log)
    out_file = steps.render(proj, background=background, log=log)
    console.print(f"[green]Done:[/] {out_file}")


@app.command()
def gui(project: Annotated[Optional[Path], typer.Argument()] = None) -> None:
    """Open the graphical editor."""
    from autokaraoke.gui.app import main

    main(project)


if __name__ == "__main__":
    app()
