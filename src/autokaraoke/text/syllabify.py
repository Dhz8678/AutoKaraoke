"""Split lyrics into words and syllables.

Alphabetic scripts use Hunspell hyphenation dictionaries (pyphen); CJK scripts
use one unit per character. Words in languages without a dictionary stay whole.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import pyphen

# Characters sung as their own unit: CJK ideographs, kana, hangul syllables.
_CJK = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]"
)
# Kana that attach to the previous mora (small kana, long vowel mark).
_ATTACHED_KANA = set("ゃゅょぁぃぅぇぉゎャュョァィゥェォヮヵヶー")


# Languages where a single vowel can be a sung syllable at a word edge ("a-mo-re").
# Others keep pyphen's typesetting minimums so "you" is not split into "y-ou".
_OPEN_SYLLABLE_LANGS = {"it", "es", "pt", "ro", "ca", "gl", "la", "eu"}


@lru_cache(maxsize=None)
def _dictionary(lang: str) -> pyphen.Pyphen | None:
    if not lang:
        return None
    base = lang.split("_")[0].split("-")[0].lower()
    for candidate in (lang, base):
        if candidate in pyphen.LANGUAGES or pyphen.language_fallback(candidate):
            edge = 1 if base in _OPEN_SYLLABLE_LANGS else 2
            return pyphen.Pyphen(lang=candidate, left=edge, right=edge)
    return None


def split_words(line: str) -> list[str]:
    """Whitespace tokenization; CJK runs are kept as one word (split later per character)."""
    return line.split()


def _split_cjk(word: str) -> list[str]:
    units: list[str] = []
    for ch in word:
        starts_unit = _CJK.match(ch) and ch not in _ATTACHED_KANA
        if units and not starts_unit:
            # Small kana, punctuation and latin runs join the previous unit.
            units[-1] += ch
        else:
            units.append(ch)
    return units


def syllabify_word(word: str, lang: str) -> list[str]:
    if _CJK.search(word):
        return _split_cjk(word)
    dic = _dictionary(lang)
    if dic is None:
        return [word]
    # Hyphenate only the letters; keep leading/trailing punctuation attached.
    m = re.match(r"^(\W*)(.*?)(\W*)$", word, flags=re.UNICODE)
    lead, core, trail = m.groups() if m else ("", word, "")
    if not core:
        return [word]
    parts: list[str] = []
    # Hyphenate each segment of compound words ("dell'amore", "rock-and-roll") separately.
    for seg in re.split(r"([-'’])", core):
        if not seg:
            continue
        if seg in "-'’" and parts:
            parts[-1] += seg
            continue
        positions = dic.positions(unicodedata.normalize("NFC", seg))
        prev = 0
        for p in positions:
            parts.append(seg[prev:p])
            prev = p
        parts.append(seg[prev:])
    parts = _merge_vowelless([p for p in parts if p])
    parts[0] = lead + parts[0]
    parts[-1] += trail
    return parts


def _has_vowel(part: str) -> bool:
    letters = [unicodedata.normalize("NFD", ch)[0].lower() for ch in part if ch.isalpha()]
    if any(not ("a" <= ch <= "z") for ch in letters):
        return True  # non-Latin script: no opinion, keep the split
    return any(ch in "aeiouy" for ch in letters)


def _merge_vowelless(parts: list[str]) -> list[str]:
    """Apostrophe splits leave pieces like "C'" or "t" that are not syllables.

    An elided prefix ("C'", "l'") joins the next part ("C'è"); a suffix ("t" in
    "don't") joins the previous one.
    """
    out: list[str] = []
    carry = ""
    for p in parts:
        p = carry + p
        carry = ""
        if _has_vowel(p) or len(parts) == 1:
            out.append(p)
        elif p.endswith(("'", "’")):
            carry = p
        elif out:
            out[-1] += p
        else:
            carry = p
    if carry:
        if out:
            out[-1] += carry
        else:
            out.append(carry)
    return out


def syllabify_line(line: str, lang: str) -> list[list[str]]:
    """Return a list of words, each a list of syllables."""
    return [syllabify_word(w, lang) for w in split_words(line)]


def detect_language(text: str) -> str:
    from autokaraoke.text.chinese import script_language

    hint = script_language(text)
    if hint:
        return hint
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        language = detect(text)
        return "zh" if language in ("zh-cn", "zh-tw") else language
    except Exception:
        return ""
