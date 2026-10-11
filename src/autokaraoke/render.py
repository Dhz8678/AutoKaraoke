"""Render the karaoke video with ffmpeg + libass, muxing one audio track per mix."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autokaraoke.audio import probe_duration, require_ffmpeg
from autokaraoke.export.ass import AssStyle, project_style, write_ass
from autokaraoke.mix import karaoke_mix
from autokaraoke.project import Project


def render(
    project: Project,
    output: Path,
    background: Path | None = None,
    background_color: str | None = None,
    style: AssStyle | None = None,
    fps: int = 30,
) -> Path:
    require_ffmpeg()
    st = style or project_style(project)
    assert project.path is not None
    ass_path = write_ass(project, project.path / "lyrics.ass", st)
    if background is None and st.background_image and Path(st.background_image).exists():
        background = Path(st.background_image)
    color = background_color or st.background_color
    if color.startswith("#"):
        color = "0x" + color[1:]

    # (file, track title); the first available one becomes the default track.
    # With backing lines the default is the instrumental plus those vocals.
    mix = karaoke_mix(project)
    tracks = [
        (mix, "Karaoke (with backing vocals)"),
        (project.abspath(project.audio_instrumental), "Instrumental"),
        (project.abspath(project.audio_full), "Original"),
        (project.abspath(project.audio_vocals), "Vocals only"),
    ]
    tracks = [(p, title) for p, title in tracks if p and p.exists()]
    if not tracks:
        raise RuntimeError("project has no audio")
    duration = project.duration or probe_duration(tracks[0][0])
    size = f"{st.width}x{st.height}"

    cmd = ["ffmpeg", "-y", "-v", "error", "-stats"]
    if background:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", str(background.resolve())]
        scale = (
            f"scale={size.replace('x', ':')}:force_original_aspect_ratio=increase,"
            f"crop={size.replace('x', ':')},"
        )
    else:
        cmd += ["-f", "lavfi", "-i", f"color=c={color}:s={size}:r={fps}:d={duration:.3f}"]
        scale = ""
    for p, _ in tracks:
        cmd += ["-i", str(p.resolve())]

    # The ass filter path is relative to cwd (project dir) to avoid filtergraph escaping issues.
    cmd += ["-vf", f"{scale}ass={ass_path.name}", "-map", "0:v"]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    for i, (_, title) in enumerate(tracks):
        cmd += [f"-metadata:s:a:{i}", f"title={title}",
                f"-disposition:a:{i}", "default" if i == 0 else "0"]
    cmd += ["-metadata", f"title={project.artist} - {project.title}"]
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd.append(str(output))
    subprocess.run(cmd, check=True, cwd=project.path)
    return output
