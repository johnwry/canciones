#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files by combining:

  songs/txt/himnos          = clean curated hymn lyrics and stanza formatting
  songs/txt/himnos-from-pdf = raw PDF-extracted hymn text with chord lines

Output:

  songs/cho/himnos-acordes

Run from repository root:

  python3 scripts/build_himnos_acordes_from_pdf_txt.py

Important:
- The clean TXT file is the layout authority.
- Blank lines/stanzas from songs/txt/himnos are preserved.
- The PDF TXT file is used only to recover chord groups.
- Originals are never modified.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import re
import shutil
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
CLEAN_DIR = ROOT / "songs" / "txt" / "himnos"
PDF_TXT_DIR = ROOT / "songs" / "txt" / "himnos-from-pdf"
OUT_DIR = ROOT / "songs" / "cho" / "himnos-acordes"

TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
CHORD_TOKEN_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$"
)
CHORD_SCAN_RE = re.compile(
    r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?"
)
SECTION_WORDS = {"coro", "chorus", "estrofa", "verse"}
BAD_SINGLE_CHORD_WORDS = {"A", "Y"}


@dataclass
class SourceLine:
    chords: list[str]
    lyric: str


def clean_spaces(s: str) -> str:
    return (
        s.replace("\u00a0", " ")
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("￾", "")
        .replace("—", "-")
        .strip()
    )


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_for_match(s: str) -> str:
    s = re.sub(r"\[[^\]]+\]", "", s)
    s = re.sub(r"\{[^}]+\}", "", s)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9ñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def hymn_number(path: Path) -> int | None:
    m = re.match(r"^(\d{1,3})", path.stem)
    return int(m.group(1)) if m else None


def title_from_path(path: Path) -> str:
    stem = re.sub(r"^\d+\s*[-.]\s*", "", path.stem).strip()
    return stem.upper() if stem else path.stem


def read_clean_layout(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = clean_spaces(raw_line)
        if TITLE_RE.match(line):
            continue
        if line.upper() == "INDICE":
            continue
        lines.append(line)

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def is_chord_token(token: str) -> bool:
    return bool(CHORD_TOKEN_RE.fullmatch(token.strip().strip(".,;:()")))


def split_chord_stream(token: str) -> list[str] | None:
    token = token.strip().strip(".,;:()")
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


def chord_pieces_from_line(line: str) -> list[str]:
    pieces: list[str] = []
    # Replace periods because the PDF text sometimes has B. Em instead of B Em.
    line = clean_spaces(line).replace(".", " ")
    for token in line.split():
        split = split_chord_stream(token)
        if not split:
            return []
        pieces.extend(split)
    return pieces


def is_chord_line(line: str) -> bool:
    text = clean_spaces(line)
    if not text:
        return False
    if text.lower().rstrip(":") in SECTION_WORDS:
        return False
    pieces = chord_pieces_from_line(text)
    if not pieces:
        return False
    if len(pieces) == 1 and pieces[0] in BAD_SINGLE_CHORD_WORDS:
        return False
    return True


def token_is_chordish(token: str) -> bool:
    token = token.strip().strip(".,;:()")
    if not token:
        return False
    if token in BAD_SINGLE_CHORD_WORDS:
        return False
    return split_chord_stream(token) is not None


def split_mixed_chord_lyric_line(line: str) -> tuple[list[str], str]:
    """Extract isolated chord tokens from a mixed PDF line.

    Examples from the extracted PDF text:
      "G C D G Voz de amor y de clemencia"
      "Del trono celestial D G A G A D"
      "Gozo tenemos por Cristo Jesús, D G"

    This is intentionally aggressive because these files are chord-source files,
    not the final lyrics. The final lyric line comes from the clean TXT file.
    """
    tokens = clean_spaces(line).replace(".", " ").split()
    chords: list[str] = []
    lyric_tokens: list[str] = []

    for token in tokens:
        cleaned = token.strip().strip(".,;:()")
        if token_is_chordish(cleaned):
            pieces = split_chord_stream(cleaned) or []
            chords.extend(pieces)
        else:
            lyric_tokens.append(token)

    lyric = clean_spaces(" ".join(lyric_tokens))
    return chords, lyric


def parse_pdf_txt_source(path: Path) -> list[SourceLine]:
    """Parse raw PDF-extracted TXT into chord+lyric source lines."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    source: list[SourceLine] = []
    pending_chords: list[str] = []

    for raw_line in raw.splitlines():
        line = clean_spaces(raw_line)
        if not line:
            continue
        if line.upper() == "INDICE":
            continue
        # Skip heading if still present.
        if re.match(r"^\d{1,3}\.\s+", line):
            pending_chords = []
            continue
        if line.lower().rstrip(":") in SECTION_WORDS:
            pending_chords = []
            continue

        if is_chord_line(line):
            pending_chords.extend(chord_pieces_from_line(line))
            continue

        inline_chords, source_lyric = split_mixed_chord_lyric_line(line)
        all_chords = pending_chords + inline_chords
        pending_chords = []

        if source_lyric:
            source.append(SourceLine(all_chords, source_lyric))

    return source


def attach_chords(chords: list[str], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric

    words = lyric.split()
    if len(words) <= 1:
        return "".join(f"[{ch}]" for ch in chords) + lyric

    # Because PDF text extraction loses horizontal spacing, distribute chord groups
    # across the lyric line. The important thing is that all chords are preserved
    # inline and no chord-only lines remain.
    slots: dict[int, list[str]] = {}
    if len(chords) == 1:
        slots[0] = chords
    else:
        for i, chord in enumerate(chords):
            idx = round(i * (len(words) - 1) / (len(chords) - 1))
            slots.setdefault(idx, []).append(chord)

    return " ".join(
        "".join(f"[{ch}]" for ch in slots.get(i, [])) + word
        for i, word in enumerate(words)
    )


def best_source_for_line(
    clean_line: str,
    source_lines: list[SourceLine],
    used: set[int],
    hint: int,
) -> tuple[int | None, float]:
    target = normalize_for_match(clean_line)
    if not target:
        return None, 0.0

    best_i: int | None = None
    best_score = 0.0

    candidates = list(range(hint, min(len(source_lines), hint + 18)))
    candidates += [i for i in range(len(source_lines)) if i not in candidates]

    for i in candidates:
        if i in used:
            continue
        score = difflib.SequenceMatcher(None, target, normalize_for_match(source_lines[i].lyric)).ratio()
        if score > best_score:
            best_i = i
            best_score = score
        if score >= 0.98:
            break

    return best_i, best_score


def build_one(clean_path: Path, source_path: Path, out_path: Path) -> dict[str, int]:
    clean_lines = read_clean_layout(clean_path)
    source_lines = parse_pdf_txt_source(source_path)

    output: list[str] = [f"{{title: {title_from_path(clean_path)}}}", ""]
    used: set[int] = set()
    hint = 0

    matched = 0
    chorded = 0
    unchorded = 0

    for line in clean_lines:
        if line == "":
            if output and output[-1] != "":
                output.append("")
            continue

        if line.lower().rstrip(":") in SECTION_WORDS:
            output.append(f"{{comment: {line.rstrip(':').capitalize()}}}")
            continue

        idx, score = best_source_for_line(line, source_lines, used, hint)
        if idx is not None and score >= 0.64:
            src = source_lines[idx]
            used.add(idx)
            hint = idx + 1
            matched += 1
            if src.chords:
                chorded += 1
            else:
                unchorded += 1
            output.append(attach_chords(src.chords, line))
        else:
            unchorded += 1
            output.append(line)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return {"matched": matched, "chorded": chorded, "unchorded": unchorded}


def choose_source_file(clean_path: Path, source_files: list[Path]) -> Path | None:
    n = hymn_number(clean_path)
    if n is not None:
        for p in source_files:
            if hymn_number(p) == n:
                return p

    clean_name = normalize_for_match(clean_path.stem)
    best: Path | None = None
    best_score = 0.0
    for p in source_files:
        score = difflib.SequenceMatcher(None, clean_name, normalize_for_match(p.stem)).ratio()
        if score > best_score:
            best = p
            best_score = score
    return best if best_score >= 0.72 else None


def main() -> None:
    if not CLEAN_DIR.exists():
        raise SystemExit(f"Missing clean hymn folder: {CLEAN_DIR}")
    if not PDF_TXT_DIR.exists():
        raise SystemExit(f"Missing PDF-extracted hymn folder: {PDF_TXT_DIR}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    clean_files = sorted(CLEAN_DIR.glob("*.txt"))
    source_files = sorted(PDF_TXT_DIR.glob("*.txt"))

    created = 0
    skipped = 0
    total_matched = 0
    total_chorded = 0
    total_unchorded = 0

    for clean_path in clean_files:
        source_path = choose_source_file(clean_path, source_files)
        if not source_path:
            skipped += 1
            continue

        out_path = OUT_DIR / f"{clean_path.stem}.cho"
        stats = build_one(clean_path, source_path, out_path)
        created += 1
        total_matched += stats["matched"]
        total_chorded += stats["chorded"]
        total_unchorded += stats["unchorded"]

    print(f"Created: {created} files in {OUT_DIR.relative_to(ROOT)}")
    print(f"Skipped clean TXT files without PDF source: {skipped}")
    print(f"Matched lyric lines: {total_matched}")
    print(f"Lines with inserted chords: {total_chorded}")
    print(f"Lines without chords: {total_unchorded}")


if __name__ == "__main__":
    main()
