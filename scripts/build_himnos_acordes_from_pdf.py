#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files from the chorded PDF and clean TXT hymns.

This version uses the PDF's embedded/selectable text first. That is much faster
than OCR and works better for ACORDES HIMNOS.pdf because many pages already
contain extracted text with chord lines.

Input:
  songs/txt/himnos = clean lyrics and stanza formatting
  PDF              = chord source

Output:
  songs/cho/himnos-acordes

Examples:
  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf --hymns 517
  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf --pages 293
  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf

TXT files remain the layout authority, so stanza/chorus blank lines are preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import difflib
import re
import shutil
import unicodedata

try:
    import fitz  # PyMuPDF
except ImportError as exc:
    raise SystemExit("Missing dependency: pymupdf. Install with: python3 -m pip install pymupdf") from exc

ROOT = Path(__file__).resolve().parents[1]
TXT_DIR = ROOT / "songs" / "txt" / "himnos"
OUT_DIR = ROOT / "songs" / "cho" / "himnos-acordes"

HEADING_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+?)\s*$")
TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
CHORD_TOKEN_RE = re.compile(r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$")
CHORD_SCAN_RE = re.compile(r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?")
SECTION_WORDS = {"coro", "chorus", "estrofa", "verse"}
BAD_SINGLE_CHORD_WORDS = {"A", "Y"}


@dataclass
class SourceLine:
    hymn_number: int
    page_number: int
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
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize_for_match(s: str) -> str:
    s = re.sub(r"\[[^\]]+\]", "", s)
    s = re.sub(r"\{[^}]+\}", "", s)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9ñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_range(spec: str | None, maximum: int | None = None) -> set[int] | None:
    if not spec:
        return None
    selected: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            selected.update(range(int(a), int(b) + 1))
        else:
            selected.add(int(part))
    if maximum is not None:
        selected = {n for n in selected if 1 <= n <= maximum}
    return selected


def hymn_number_from_path(path: Path) -> int | None:
    m = re.match(r"^(\d{1,3})", path.stem)
    return int(m.group(1)) if m else None


def title_from_txt_path(txt_path: Path) -> str:
    stem = re.sub(r"^\d+\s*[-.]\s*", "", txt_path.stem).strip()
    return stem.upper() if stem else txt_path.stem


def read_txt_layout(txt_path: Path) -> list[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines: list[str] = []
    for raw_line in raw.splitlines():
        line = clean_spaces(raw_line)
        if TITLE_RE.match(line) or line.upper() == "INDICE":
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
    # Fix common PDF extraction/OCR artifact: B. E should be B E, handled at line split level.
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
    line = clean_spaces(line).replace(".", " ")
    for token in line.split():
        split = split_chord_stream(token)
        if not split:
            return []
        pieces.extend(split)
    return pieces


def is_chord_line_text(line: str) -> bool:
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


def extract_sources_from_pdf_text(pdf_path: Path, page_filter: set[int] | None, hymn_filter: set[int] | None) -> dict[int, list[SourceLine]]:
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    pages = sorted(page_filter or set(range(1, total_pages + 1)))
    sources: dict[int, list[SourceLine]] = {}

    current_hymn: int | None = None
    pending_chords: list[str] = []

    for count, page_number in enumerate(pages, 1):
        print(f"Reading PDF text page {page_number}/{total_pages} ({count}/{len(pages)})...", flush=True)
        page_text = doc[page_number - 1].get_text("text") or ""
        for raw in page_text.splitlines():
            line = clean_spaces(raw)
            if not line:
                continue
            if line.upper() == "INDICE":
                continue

            heading = HEADING_RE.match(line)
            if heading:
                current_hymn = int(heading.group(1))
                pending_chords = []
                continue

            if current_hymn is None:
                continue
            if hymn_filter is not None and current_hymn not in hymn_filter:
                continue

            if line.lower().rstrip(":") in SECTION_WORDS:
                pending_chords = []
                continue

            if is_chord_line_text(line):
                pending_chords.extend(chord_pieces_from_line(line))
                continue

            # Lyric line: attach the chord lines immediately above it.
            sources.setdefault(current_hymn, []).append(
                SourceLine(current_hymn, page_number, pending_chords, line)
            )
            pending_chords = []

    print(f"PDF text source hymns found: {len(sources)}", flush=True)
    return sources


def attach_chords(chords: list[str], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric
    words = lyric.split()
    if len(words) <= 1:
        return "".join(f"[{ch}]" for ch in chords) + lyric

    # PDF text extraction usually loses exact horizontal spacing. This distributes
    # recovered chord groups across the lyric line while preserving all chords.
    slots: dict[int, list[str]] = {}
    if len(chords) == 1:
        slots[0] = chords
    else:
        for i, chord in enumerate(chords):
            idx = round(i * (len(words) - 1) / (len(chords) - 1))
            slots.setdefault(idx, []).append(chord)

    return " ".join("".join(f"[{ch}]" for ch in slots.get(i, [])) + word for i, word in enumerate(words))


def best_source_for_txt_line(txt_line: str, source_lines: list[SourceLine], used: set[int], start_hint: int) -> tuple[int | None, float]:
    target = normalize_for_match(txt_line)
    if not target:
        return None, 0.0
    best_i: int | None = None
    best_score = 0.0
    candidates = list(range(start_hint, min(len(source_lines), start_hint + 15)))
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


def build_one_hymn(txt_path: Path, source_lines: list[SourceLine]) -> tuple[str, dict[str, int]]:
    txt_lines = read_txt_layout(txt_path)
    out: list[str] = [f"{{title: {title_from_txt_path(txt_path)}}}", ""]
    used: set[int] = set()
    hint = 0
    matched = chorded = unchorded = 0

    for line in txt_lines:
        if line == "":
            if out and out[-1] != "":
                out.append("")
            continue
        if line.lower().rstrip(":") in SECTION_WORDS:
            out.append(f"{{comment: {line.rstrip(':').capitalize()}}}")
            continue

        idx, score = best_source_for_txt_line(line, source_lines, used, hint)
        if idx is not None and score >= 0.64:
            src = source_lines[idx]
            used.add(idx)
            hint = idx + 1
            matched += 1
            if src.chords:
                chorded += 1
            else:
                unchorded += 1
            out.append(attach_chords(src.chords, line))
        else:
            unchorded += 1
            out.append(line)

    return "\n".join(out).rstrip() + "\n", {"matched": matched, "chorded": chorded, "unchorded": unchorded}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ChordPro hymns from clean TXT files and chorded PDF text.")
    parser.add_argument("pdf", help="Path to ACORDES HIMNOS.pdf")
    parser.add_argument("--pages", help="Page range to read, e.g. 293 or 8-20")
    parser.add_argument("--hymns", help="Only build selected hymn numbers, e.g. 517 or 1-40")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if not TXT_DIR.exists():
        raise SystemExit(f"Missing clean lyrics folder: {TXT_DIR}")

    doc = fitz.open(pdf_path)
    page_filter = parse_range(args.pages, len(doc))
    hymn_filter = parse_range(args.hymns)

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    sources = extract_sources_from_pdf_text(pdf_path, page_filter, hymn_filter)

    txt_files = sorted(TXT_DIR.glob("*.txt"))
    created = skipped = total_matched = total_chorded = total_unchorded = 0
    for txt_path in txt_files:
        hymn_number = hymn_number_from_path(txt_path)
        if hymn_filter is not None and hymn_number not in hymn_filter:
            continue
        if hymn_number is None or hymn_number not in sources:
            skipped += 1
            continue
        content, stats = build_one_hymn(txt_path, sources[hymn_number])
        (OUT_DIR / f"{txt_path.stem}.cho").write_text(content, encoding="utf-8")
        created += 1
        total_matched += stats["matched"]
        total_chorded += stats["chorded"]
        total_unchorded += stats["unchorded"]

    print(f"Created: {created} files in {OUT_DIR.relative_to(ROOT)}")
    print(f"Skipped TXT hymns without PDF chord source: {skipped}")
    print(f"Matched lyric lines: {total_matched}")
    print(f"Lines with inserted chords: {total_chorded}")
    print(f"Lines without chords: {total_unchorded}")


if __name__ == "__main__":
    main()
