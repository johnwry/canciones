#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files by combining:

  songs/txt/himnos = clean hymn lyrics and stanza formatting
  songs/cho/himnos = chord source files

Output:

  songs/cho/himnos-acordes

Run from the repository root:

  python3 scripts/build_himnos_acordes.py

Important behavior:
- The TXT file is the layout authority.
- Blank lines/stanzas from TXT are preserved.
- Existing CHO files are used only as chord sources.
- Originals are never modified.
"""

from __future__ import annotations

from pathlib import Path
import difflib
import re
import shutil
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
TXT_DIR = ROOT / "songs" / "txt" / "himnos"
CHO_DIR = ROOT / "songs" / "cho" / "himnos"
OUT_DIR = ROOT / "songs" / "cho" / "himnos-acordes"

TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
COMMENT_RE = re.compile(r"^\{comment:\s*(.*?)\s*\}$", re.IGNORECASE)
INLINE_CHORD_RE = re.compile(r"\[([^\]]+)\]")
CHORD_TOKEN_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$"
)
CHORD_SCAN_RE = re.compile(
    r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?"
)

SECTION_WORDS = {"coro", "chorus", "estrofa", "verse"}


def clean_spaces(s: str) -> str:
    return (
        s.replace("\u00a0", " ")
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("￾", "")
        .rstrip()
    )


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_for_match(s: str) -> str:
    s = INLINE_CHORD_RE.sub("", s)
    s = COMMENT_RE.sub("", s)
    s = re.sub(r"\{[^}]+\}", "", s)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9ñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def comment_payload(line: str) -> str | None:
    m = COMMENT_RE.match(line.strip())
    if not m:
        return None
    return clean_spaces(m.group(1)).strip()


def is_chord_token(token: str) -> bool:
    return bool(CHORD_TOKEN_RE.fullmatch(token.strip().strip(".")))


def split_chord_stream(token: str) -> list[str] | None:
    """Split glued chord strings like C#7F#m or F#mG#m."""
    token = token.strip().strip(".")
    if not token:
        return None
    if is_chord_token(token):
        return [token]

    pieces: list[str] = []
    i = 0
    while i < len(token):
        m = CHORD_SCAN_RE.match(token, i)
        if not m:
            return None
        pieces.append(m.group(0))
        i = m.end()
    return pieces or None


def chord_tokens_from_text(text: str) -> list[str]:
    text = clean_spaces(text).strip()
    payload = comment_payload(text)
    if payload is not None:
        text = payload

    tokens: list[str] = []
    for raw in text.split():
        pieces = split_chord_stream(raw)
        if not pieces:
            return []
        tokens.extend(pieces)
    return tokens


def is_chord_only_line(line: str) -> bool:
    line = clean_spaces(line).strip()
    if not line:
        return False

    payload = comment_payload(line)
    test = payload if payload is not None else line
    if test.lower().rstrip(":") in SECTION_WORDS:
        return False

    if INLINE_CHORD_RE.fullmatch(test):
        return True

    return bool(chord_tokens_from_text(test))


def chords_from_line(line: str) -> list[str]:
    line = clean_spaces(line).strip()
    payload = comment_payload(line)
    if payload is not None:
        return chord_tokens_from_text(payload)

    inline = INLINE_CHORD_RE.findall(line)
    if inline:
        return [c.strip() for c in inline if c.strip()]

    return chord_tokens_from_text(line)


def lyric_from_cho_line(line: str) -> str:
    line = clean_spaces(line).strip()
    line = INLINE_CHORD_RE.sub("", line)
    return line.strip()


def title_from_cho(text: str, fallback: str) -> str:
    for line in text.splitlines():
        m = TITLE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return fallback


def title_from_txt(txt_path: Path) -> str:
    stem = txt_path.stem
    stem = re.sub(r"^\d+\s*[-.]\s*", "", stem).strip()
    return stem.upper() if stem else txt_path.stem


def read_txt_layout(txt_path: Path) -> list[str]:
    """Read TXT exactly as layout base, preserving blank lines."""
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = clean_spaces(raw_line).strip()
        if TITLE_RE.match(line):
            continue
        if line.upper() == "INDICE":
            continue
        # Preserve blanks because they mark stanzas/slides.
        lines.append(line)

    # Trim leading/trailing blank lines only.
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def parse_chord_source(cho_path: Path) -> tuple[str, list[tuple[list[str], str]]]:
    """Parse source into ordered (chords, lyric) pairs.

    Handles all these cases:
    - plain chord line above lyric: A D E / Gracias...
    - comment chord line above lyric: {comment: A D E} / Gracias...
    - orphan bracket chord lines: [A] / [E] / lyric
    - already inline chord line: [A]Gracias...
    """
    text = cho_path.read_text(encoding="utf-8", errors="ignore")
    title = title_from_cho(text, cho_path.stem)
    pairs: list[tuple[list[str], str]] = []
    pending_chords: list[str] = []

    for raw in text.splitlines():
        line = clean_spaces(raw).strip()
        if not line:
            continue
        if TITLE_RE.match(line):
            continue
        if line.upper() == "INDICE":
            continue
        if line.lower().startswith("{comment: himno") or line.lower().startswith("{comment: fuente"):
            continue

        payload = comment_payload(line)
        if payload is not None:
            if payload.lower().rstrip(":") in SECTION_WORDS:
                pairs.append(([], payload))
                continue
            if is_chord_only_line(line):
                pending_chords.extend(chords_from_line(line))
                continue
            # Non-chord comments are labels; preserve them as lyrics/comments for matching only if useful.
            pairs.append(([], payload))
            continue

        if line.lower().rstrip(":") in SECTION_WORDS:
            pairs.append(([], line))
            continue

        if is_chord_only_line(line):
            pending_chords.extend(chords_from_line(line))
            continue

        inline_chords = chords_from_line(line)
        lyric = lyric_from_cho_line(line)
        chords = pending_chords + inline_chords
        pending_chords = []
        if lyric:
            pairs.append((chords, lyric))

    # Do not emit final orphan chords; without a lyric they cannot be placed safely.
    return title, pairs


def attach_chords(chords: list[str], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric
    if not lyric:
        return "".join(f"[{c}]" for c in chords)

    words = lyric.split()
    if len(words) <= 1:
        return "".join(f"[{c}]" for c in chords) + lyric

    # With no original column data, distribute chords over word starts.
    # This is not musically perfect, but it keeps every chord inline and usable.
    slots: dict[int, list[str]] = {}
    if len(chords) == 1:
        slots[0] = chords
    else:
        for i, chord in enumerate(chords):
            word_index = round(i * (len(words) - 1) / (len(chords) - 1))
            slots.setdefault(word_index, []).append(chord)

    output_words: list[str] = []
    for i, word in enumerate(words):
        prefix = "".join(f"[{c}]" for c in slots.get(i, []))
        output_words.append(prefix + word)
    return " ".join(output_words)


def best_chord_pair_for_line(
    clean_line: str,
    chord_pairs: list[tuple[list[str], str]],
    used_pairs: set[int],
    start_hint: int,
) -> tuple[int | None, float]:
    target = normalize_for_match(clean_line)
    if not target:
        return None, 0.0

    best_i: int | None = None
    best_score = 0.0

    # Prefer nearby chord-source lines first. This keeps repeated chorus/stanza lines sane.
    candidates = list(range(start_hint, min(len(chord_pairs), start_hint + 12)))
    candidates += [i for i in range(len(chord_pairs)) if i not in candidates]

    for i in candidates:
        if i in used_pairs:
            continue
        _chords, source_lyric = chord_pairs[i]
        source = normalize_for_match(source_lyric)
        if not source:
            continue
        score = difflib.SequenceMatcher(None, target, source).ratio()
        if score > best_score:
            best_i = i
            best_score = score
        if score >= 0.98:
            break

    return best_i, best_score


def merge_one(txt_path: Path, cho_path: Path, out_path: Path) -> dict[str, int]:
    txt_lines = read_txt_layout(txt_path)
    cho_title, chord_pairs = parse_chord_source(cho_path)
    title = cho_title or title_from_txt(txt_path)

    used_pairs: set[int] = set()
    pair_hint = 0
    output: list[str] = [f"{{title: {title}}}", ""]
    matched = 0
    chorded = 0
    unchorded = 0

    for line in txt_lines:
        # Preserve stanza/slide breaks from the TXT source.
        if line == "":
            if output and output[-1] != "":
                output.append("")
            continue

        # Preserve section labels from TXT.
        if line.lower().rstrip(":") in SECTION_WORDS:
            output.append(f"{{comment: {line.rstrip(':').capitalize()}}}")
            continue

        idx, score = best_chord_pair_for_line(line, chord_pairs, used_pairs, pair_hint)
        if idx is not None and score >= 0.68:
            chords, _source_lyric = chord_pairs[idx]
            used_pairs.add(idx)
            pair_hint = idx + 1
            matched += 1
            if chords:
                chorded += 1
            else:
                unchorded += 1
            output.append(attach_chords(chords, line))
        else:
            unchorded += 1
            output.append(line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(output).rstrip() + "\n"
    out_path.write_text(text, encoding="utf-8")
    return {"matched": matched, "chorded": chorded, "unchorded": unchorded}


def choose_matching_txt(cho_path: Path, txt_files: list[Path]) -> Path | None:
    # Same stem first.
    for p in txt_files:
        if p.stem == cho_path.stem:
            return p

    # Hymn number match.
    cho_num = re.match(r"^(\d+)", cho_path.stem)
    if cho_num:
        n = cho_num.group(1).lstrip("0")
        for p in txt_files:
            m = re.match(r"^(\d+)", p.stem)
            if m and m.group(1).lstrip("0") == n:
                return p

    # Fuzzy filename fallback.
    cho_name = normalize_for_match(cho_path.stem)
    best: Path | None = None
    best_score = 0.0
    for p in txt_files:
        score = difflib.SequenceMatcher(None, cho_name, normalize_for_match(p.stem)).ratio()
        if score > best_score:
            best = p
            best_score = score
    return best if best_score >= 0.72 else None


def main() -> None:
    if not TXT_DIR.exists():
        raise SystemExit(f"Missing clean lyrics folder: {TXT_DIR}")
    if not CHO_DIR.exists():
        raise SystemExit(f"Missing chord source folder: {CHO_DIR}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    txt_files = sorted(TXT_DIR.glob("*.txt"))
    cho_files = sorted(CHO_DIR.glob("*.cho"))

    created = 0
    skipped = 0
    total_matched = 0
    total_chorded = 0
    total_unchorded = 0

    for cho_path in cho_files:
        txt_path = choose_matching_txt(cho_path, txt_files)
        if not txt_path:
            skipped += 1
            continue

        out_path = OUT_DIR / cho_path.name
        stats = merge_one(txt_path, cho_path, out_path)
        created += 1
        total_matched += stats["matched"]
        total_chorded += stats["chorded"]
        total_unchorded += stats["unchorded"]

    print(f"Created: {created} files in {OUT_DIR.relative_to(ROOT)}")
    print(f"Skipped chord files without matching txt: {skipped}")
    print(f"Matched lyric lines: {total_matched}")
    print(f"Lines with inserted chords: {total_chorded}")
    print(f"Lines without chords: {total_unchorded}")


if __name__ == "__main__":
    main()
