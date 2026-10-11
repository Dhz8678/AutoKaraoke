# AutoKaraoke

Semi-automatic karaoke track generator with syllable-level sync.

Pipeline: audio → lyrics from [LRCLIB](https://lrclib.net) → vocal separation (UVR models via
[python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)) → automatic syllable
alignment → manual fine-tuning in the editor → video with instrumental, original and vocals-only
audio tracks.

> Work in progress (MVP).

## Install

Requires [uv](https://docs.astral.sh/uv/), `ffmpeg` (with libass) in `PATH`, and preferably an
NVIDIA GPU (CPU works, slowly).

```sh
uv sync --all-extras        # or pick: --extra separation --extra align --extra gui [--extra download]
```

## Usage

### Editor (GUI)

```sh
uv run autokaraoke gui ["projects/Artist - Title"]
```

*New* → pick an audio file → *Separate* → *Auto-sync* → fix what is highlighted → *Render video*.

- **Lyrics**: from LRCLIB, from a local `.lrc`/`.txt`, or *Transcribe from vocals* when no lyrics
  exist anywhere.

- **Timeline**: drag syllable blocks or their edges (touching neighbours follow, Alt detaches);
  colors show confidence (green ok, yellow/red to check, blue edited by you).
- **Tap sync**: choose *Hold* (press = syllable start, release = end) or *Tap* (each tap starts a
  syllable), select the first syllable, then press Space along with the song. Slow it down to
  0.5× if needed; adjust the latency compensation to your reaction time.
- `[` / `]` set the selected syllable start/end at the playhead; double-click a lyric line to edit
  its text and syllable splits (`cuo|re`). F1 lists all shortcuts.

### Command line

```sh
uv run autokaraoke run "Artist - Title.mp3"          # whole pipeline

uv run autokaraoke new song.flac --artist "Artist" --title "Title"
uv run autokaraoke separate "projects/Artist - Title"
uv run autokaraoke lyrics "projects/Artist - Title" --from-audio   # only if LRCLIB has nothing
uv run autokaraoke reconcile "projects/Artist - Title"   # experimental, see below
uv run autokaraoke align "projects/Artist - Title"    # --engine qwen|mms|naive
uv run autokaraoke render "projects/Artist - Title"   # --background image.jpg
uv run autokaraoke export "projects/Artist - Title" --format lrc
```

## How the alignment works

1. Lyrics are split into syllables (Hunspell hyphenation via `pyphen`, one unit per character for
   CJK).
2. **[Qwen3-ForcedAligner-0.6B](https://github.com/QwenLM/Qwen3-ASR)** aligns each LRC line on the
   separated vocals, with its neighbouring lines as context. A timestamp slot follows every
   syllable.
3. **MMS CTC aligner** (`mms-300m-1130-forced-aligner`, ~1100 languages through `uroman`) aligns
   the same lines independently. Where the two disagree, syllables are flagged; where Qwen loses
   track (layered backing vocals, "ba-dee-ya" refrains), the MMS result is used instead.
4. Lyrics without timestamps (plain text, or transcribed) are first located in a
   **[Qwen3-ASR-1.7B](https://github.com/QwenLM/Qwen3-ASR)** transcript of the vocals by fuzzy word
   matching.
5. Post-processing fixes known failure modes (first syllable glued to the previous line, zero-length
   syllables) using the vocal energy envelope, and assigns a confidence to every syllable.

## Lyric check with an LLM (experimental, off by default)

The results are hit and miss, so this is disabled unless you enable it in *File → Settings…*
(or set `AUTOKARAOKE_EXPERIMENTAL_LLM=1`). It then adds *Fix lyrics (experimental)…*, which
proposes changes (expanded choruses, removed `[Chorus]` labels, wrong words) to accept or reject
one by one, and cleans up lyrics transcribed from audio.

Published lyrics often differ from the recording. With an [OpenRouter](https://openrouter.ai) key,
the vocals are transcribed locally (Qwen3-ASR) and a text-only LLM (default
`deepseek/deepseek-v4.1-flash`, ~$0.002 per song) compares the two. The LLM only edits text, never
timing: language models that accept audio are not precise enough for timestamps.

Set the key in *File → Settings…*, or as `OPENROUTER_API_KEY` in the environment or in a `.env`
file (current folder or `~/.config/autokaraoke/.env`). `AUTOKARAOKE_LLM_MODEL` picks another model.

## Audio download

Downloading from YouTube / YT Music (`yt-dlp`) is only available when running from source with the
`download` extra. Release builds ship with it disabled: please use audio files you own.

## Licenses of models

| Model | Use | License |
|---|---|---|
| Qwen3-ForcedAligner-0.6B | alignment | Apache 2.0 |
| Qwen3-ASR-1.7B | transcription for lyric check / unsynced lyrics | Apache 2.0 |
| MMS-300M forced aligner | alignment fallback / cross-check | CC-BY-NC 4.0 (non-commercial) |
| MelBand RoFormer (Kim) | vocal separation | see model card |
