"""LRCLIB.net client and LRC parser."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from autokaraoke.config import USER_AGENT

API = "https://lrclib.net/api"
# LRCLIB matches durations within +-2 s; we allow a bit more when searching.
DURATION_TOLERANCE = 5.0


@dataclass
class LyricsResult:
    id: int
    track: str
    artist: str
    album: str
    duration: float
    plain: str
    synced: str
    instrumental: bool

    @classmethod
    def from_json(cls, d: dict) -> LyricsResult:
        return cls(
            id=d.get("id", 0),
            track=d.get("trackName") or "",
            artist=d.get("artistName") or "",
            album=d.get("albumName") or "",
            duration=float(d.get("duration") or 0),
            plain=d.get("plainLyrics") or "",
            synced=d.get("syncedLyrics") or "",
            instrumental=bool(d.get("instrumental")),
        )


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=API,
        headers={"User-Agent": USER_AGENT},
        timeout=20,
        transport=httpx.HTTPTransport(retries=2),
    )


def _get(c: httpx.Client, path: str, params: dict, attempts: int = 4) -> httpx.Response:
    """GET with backoff on 5xx: LRCLIB occasionally answers 503 under load."""
    for i in range(attempts):
        r = c.get(path, params=params)
        if r.status_code < 500 or i == attempts - 1:
            return r
        time.sleep(1.5 * 2**i)
    return r


def fetch_lyrics(
    title: str, artist: str, album: str = "", duration: float | None = None
) -> LyricsResult | None:
    """Exact lookup first, then fuzzy search preferring synced lyrics and close duration."""
    with _client() as c:
        if duration:
            params = {"track_name": title, "artist_name": artist, "duration": round(duration)}
            if album:
                params["album_name"] = album
            r = _get(c, "/get", params)
            if r.status_code == 200:
                res = LyricsResult.from_json(r.json())
                if res.synced or res.plain:
                    return res
            # On 404 or a persistent server error, fall back to search.

        r = _get(c, "/search", {"track_name": title, "artist_name": artist})
        r.raise_for_status()
        candidates = [LyricsResult.from_json(d) for d in r.json()]
        if not candidates:
            r = _get(c, "/search", {"q": f"{artist} {title}"})
            r.raise_for_status()
            candidates = [LyricsResult.from_json(d) for d in r.json()]

    candidates = [x for x in candidates if x.synced or x.plain]
    if duration:
        close = [x for x in candidates if abs(x.duration - duration) <= DURATION_TOLERANCE]
        candidates = close or candidates

    def rank(x: LyricsResult) -> tuple:
        return (not x.synced, abs(x.duration - duration) if duration else 0)

    return min(candidates, key=rank, default=None)


_TIME_TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
_META_TAG = re.compile(r"^\[[a-zA-Z]+:.*\]$")
# Enhanced LRC word tags <mm:ss.xx>, stripped from line text.
_WORD_TAG = re.compile(r"<\d+:\d+(?:[.:]\d+)?>")


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC into sorted (time, text) pairs. Empty text marks an instrumental gap."""
    out: list[tuple[float, str]] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or _META_TAG.match(raw):
            continue
        times = []
        pos = 0
        while m := _TIME_TAG.match(raw, pos):
            minutes, seconds = m.group(1), m.group(2).replace(":", ".")
            times.append(int(minutes) * 60 + float(seconds))
            pos = m.end()
        if not times:
            continue
        lyric = _WORD_TAG.sub("", raw[pos:]).strip()
        out.extend((t, lyric) for t in times)
    out.sort(key=lambda x: x[0])
    return out


def plain_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]
