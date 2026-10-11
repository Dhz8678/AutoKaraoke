"""Audio download via yt-dlp. Only available when the build enables it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autokaraoke.config import download_enabled


@dataclass
class TrackInfo:
    title: str
    artist: str
    album: str
    duration: float
    url: str


class DownloadDisabled(RuntimeError):
    pass


def download_audio(url: str, dest_dir: Path) -> tuple[Path, TrackInfo]:
    """Download the best audio stream of ``url`` into ``dest_dir``; return file and metadata."""
    if not download_enabled():
        raise DownloadDisabled(
            "Audio download is disabled in this build (or yt-dlp is not installed). "
            "Import a local audio file instead."
        )
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        file = Path(ydl.prepare_filename(info))

    artist = info.get("artist") or info.get("creator") or info.get("uploader") or ""
    # YT Music often lists several artists separated by commas; LRCLIB wants the main one.
    artist = artist.split(",")[0].strip()
    track = TrackInfo(
        title=info.get("track") or info.get("title") or "",
        artist=artist,
        album=info.get("album") or "",
        duration=float(info.get("duration") or 0),
        url=url,
    )
    return file, track
