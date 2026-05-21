#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files from the original chorded PDF images.

This is the better pipeline when the existing .cho chord-source files have lost
layout information.

Input:

  songs/txt/himnos = clean hymn lyrics and stanza formatting
  PDF              = original chorded hymn PDF, rendered/OCR'd page by page

Output:

  songs/cho/himnos-acordes

Run from repository root:

  python3 scripts/build_himnos_acordes_from_pdf.py /path/to/ACORDES\ HIMNOS.pdf

Requirements:

  brew install tesseract
  python3 -m pip install pymupdf

Optional, but helpful for Spanish OCR:

  brew install tesseract-lang

Notes:
- TXT files are the layout authority. Blank lines/stanzas are preserved.
- PDF OCR provides chord locations.
- The script tries to attach each chord line to the lyric line immediately below it.
- If a PDF page has selectable text instead of image text, the script still uses OCR
  because OCR TSV gives word coordinates, which we need for chord placement.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import difflib
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

try:
    import fitz  # PyMuPDF
except ImportError as exc:
    raise SystemExit("Missing dependency: pymupdf. Install with: python3 -m pip install pymupdf") from exc

ROOT = Path(__file__).resolve().parents[1]
TXT_DIR = ROOT / "songs" / "txt" / "himnos"
OUT_DIR = ROOT / "songs" / "cho" / "himnos-acordes"
DEBUG_DIR = ROOT / "songs" / "cho" / "himnos-acordes-debug"

HEADING_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+?)\s*$")
CHORD_TOKEN_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$"
)
CHORD_SCAN_RE = re.compile(
    r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?"
)
TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
SECTION_WORDS = {"coro", "chorus", "estrofa", "verse"}

# Words often misread as chord tokens because they are a single capital letter.
BAD_CHORD_WORDS = {"A", "Y"}


@dataclass
class OcrWord:
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float


@dataclass
class OcrLine:
    words: list[OcrWord]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def x(self) -> int:
        return min(w.x for w in self.words) if self.words else 0

    @property
    def y(self) -> int:
        return min(w.y for w in self.words) if self.words else 0

    @property
    def bottom(self) -> int:
        return max(w.y + w.h for w in self.words) if self.words else 0


@dataclass
class SourceLine:
    hymn_number: int
    page_number: int
    chords: list[tuple[int, str]]  # x coordinate, chord
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


def is_chord_token(token: str) -> bool:
    token = token.strip().strip(".,;:()")
    return bool(CHORD_TOKEN_RE.fullmatch(token))


def split_chord_stream(token: str) -> list[str] | None:
    """Split glued chord strings like C#7F#m or F#mG#m."""
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


def chord_pieces_from_line_text(line: str) -> list[str]:
    pieces: list[str] = []
    for token in clean_spaces(line).split():
        split = split_chord_stream(token)
        if not split:
            return []
        pieces.extend(split)
    return pieces


def is_chord_line(line: OcrLine) -> bool:
    text = clean_spaces(line.text)
    if not text:
        return False
    if text.lower().rstrip(":") in SECTION_WORDS:
        return False

    pieces = chord_pieces_from_line_text(text)
    if not pieces:
        return False

    # A single "A" is often the Spanish preposition, not a chord line.
    if len(pieces) == 1 and pieces[0] in BAD_CHORD_WORDS:
        return False

    return True


def chords_from_ocr_line(line: OcrLine) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for word in line.words:
        pieces = split_chord_stream(word.text)
        if not pieces:
            continue
        if len(pieces) == 1:
            out.append((word.x, pieces[0]))
        else:
            # Spread glued chords across the OCR word width.
            step = max(1, word.w // len(pieces))
            for i, piece in enumerate(pieces):
                out.append((word.x + i * step, piece))
    return out


def read_txt_layout(txt_path: Path) -> list[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
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


def title_from_txt_path(txt_path: Path) -> str:
    stem = txt_path.stem
    stem = re.sub(r"^\d+\s*[-.]\s*", "", stem).strip()
    return stem.upper() if stem else txt_path.stem


def hymn_number_from_path(path: Path) -> int | None:
    m = re.match(r"^(\d{1,3})", path.stem)
    return int(m.group(1)) if m else None


def render_page_to_png(doc: fitz.Document, page_index: int, out_path: Path, zoom: float = 2.0) -> None:
    page = doc.load_page(page_index)
    matrix = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    pix.save(out_path)


def tesseract_tsv(image_path: Path, lang: str = "spa+eng") -> list[OcrWord]:
    cmd = ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", lang, "tsv"]
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SystemExit("Missing tesseract. Install with: brew install tesseract") from exc
    except subprocess.CalledProcessError:
        # Fallback if Spanish language data is not installed.
        cmd = ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", "eng", "tsv"]
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)

    words: list[OcrWord] = []
    rows = csv.DictReader(result.stdout.splitlines(), delimiter="\t")
    for row in rows:
        text = clean_spaces(row.get("text", ""))
        if not text:
            continue
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            conf = -1
        if conf < 20:
            continue
        words.append(
            OcrWord(
                text=text,
                x=int(row.get("left", 0)),
                y=int(row.get("top", 0)),
                w=int(row.get("width", 0)),
                h=int(row.get("height", 0)),
                conf=conf,
            )
        )
    return words


def group_words_into_lines(words: list[OcrWord]) -> list[OcrLine]:
    if not words:
        return []
    words = sorted(words, key=lambda w: (w.y, w.x))
    groups: list[list[OcrWord]] = []

    for word in words:
        placed = False
        for group in groups:
            avg_y = sum(w.y for w in group) / len(group)
            avg_h = sum(w.h for w in group) / len(group)
            if abs(word.y - avg_y) <= max(8, avg_h * 0.55):
                group.append(word)
                placed = True
                break
        if not placed:
            groups.append([word])

    lines = [OcrLine(sorted(group, key=lambda w: w.x)) for group in groups]
    return sorted(lines, key=lambda l: (l.y, l.x))


def detect_hymn_heading(lines: list[OcrLine]) -> tuple[int | None, str | None]:
    # Look near the top of the page first.
    for line in lines[:8]:
        text = clean_spaces(line.text)
        m = HEADING_RE.match(text)
        if m:
            title = re.sub(r"\s+INDICE\b.*$", "", m.group(2), flags=re.IGNORECASE).strip()
            return int(m.group(1)), title
    return None, None


def pair_chords_to_lyrics(hymn_number: int, page_number: int, lines: list[OcrLine]) -> list[SourceLine]:
    source_lines: list[SourceLine] = []
    pending_chords: list[tuple[int, str]] = []

    # Skip top heading/index noise by ignoring lines above the detected heading line where possible.
    for line in lines:
        text = clean_spaces(line.text)
        if not text:
            continue
        if text.upper() == "INDICE":
            continue
        if HEADING_RE.match(text):
            continue
        if text.lower().rstrip(":") in SECTION_WORDS:
            # Chorus label has no chords, but preserving from TXT is better.
            continue

        if is_chord_line(line):
            pending_chords.extend(chords_from_ocr_line(line))
            continue

        # Treat as lyric. Attach any immediately preceding chord line(s).
        lyric = text
        if lyric:
            source_lines.append(SourceLine(hymn_number, page_number, pending_chords, lyric))
            pending_chords = []

    return source_lines


def extract_pdf_sources(pdf_path: Path) -> dict[int, list[SourceLine]]:
    doc = fitz.open(pdf_path)
    sources: dict[int, list[SourceLine]] = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for page_index in range(len(doc)):
            page_number = page_index + 1
            image_path = tmp_dir / f"page-{page_number:03d}.png"
            render_page_to_png(doc, page_index, image_path)
            words = tesseract_tsv(image_path)
            lines = group_words_into_lines(words)
            hymn_number, _title = detect_hymn_heading(lines)
            if hymn_number is None:
                continue
            source_lines = pair_chords_to_lyrics(hymn_number, page_number, lines)
            if source_lines:
                sources.setdefault(hymn_number, []).extend(source_lines)
    return sources


def insert_chords_by_x(chords: list[tuple[int, str]], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric
    if not lyric:
        return "".join(f"[{ch}]" for _x, ch in chords)

    words = lyric.split()
    if len(words) <= 1:
        return "".join(f"[{ch}]" for _x, ch in chords) + lyric

    chords = sorted(chords, key=lambda item: item[0])
    min_x = min(x for x, _ in chords)
    max_x = max(x for x, _ in chords)

    slots: dict[int, list[str]] = {}
    if max_x == min_x:
        for _x, ch in chords:
            slots.setdefault(0, []).append(ch)
    else:
        for x, ch in chords:
            rel = (x - min_x) / (max_x - min_x)
            idx = round(rel * (len(words) - 1))
            slots.setdefault(idx, []).append(ch)

    out: list[str] = []
    for i, word in enumerate(words):
        prefix = "".join(f"[{ch}]" for ch in slots.get(i, []))
        out.append(prefix + word)
    return " ".join(out)


def best_source_for_txt_line(
    txt_line: str,
    source_lines: list[SourceLine],
    used: set[int],
    start_hint: int,
) -> tuple[int | None, float]:
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
        source = normalize_for_match(source_lines[i].lyric)
        if not source:
            continue
        score = difflib.SequenceMatcher(None, target, source).ratio()
        if score > best_score:
            best_i = i
            best_score = score
        if score >= 0.98:
            break
    return best_i, best_score


def build_one_hymn(txt_path: Path, source_lines: list[SourceLine]) -> tuple[str, dict[str, int]]:
    txt_lines = read_txt_layout(txt_path)
    title = title_from_txt_path(txt_path)
    out: list[str] = [f"{{title: {title}}}", ""]
    used: set[int] = set()
    hint = 0
    matched = 0
    chorded = 0
    unchorded = 0

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
                out.append(insert_chords_by_x(src.chords, line))
            else:
                unchorded += 1
                out.append(line)
        else:
            unchorded += 1
            out.append(line)

    return "\n".join(out).rstrip() + "\n", {
        "matched": matched,
        "chorded": chorded,
        "unchorded": unchorded,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/build_himnos_acordes_from_pdf.py /path/to/ACORDES_HIMNOS.pdf")

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    if not TXT_DIR.exists():
        raise SystemExit(f"Missing clean lyrics folder: {TXT_DIR}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    sources = extract_pdf_sources(pdf_path)
    txt_files = sorted(TXT_DIR.glob("*.txt"))

    created = 0
    skipped = 0
    total_matched = 0
    total_chorded = 0
    total_unchorded = 0

    for txt_path in txt_files:
        hymn_number = hymn_number_from_path(txt_path)
        if hymn_number is None or hymn_number not in sources:
            skipped += 1
            continue

        content, stats = build_one_hymn(txt_path, sources[hymn_number])
        out_path = OUT_DIR / f"{txt_path.stem}.cho"
        out_path.write_text(content, encoding="utf-8")
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
