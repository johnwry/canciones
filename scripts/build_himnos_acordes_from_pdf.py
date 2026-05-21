#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files from the original chorded PDF images.

TXT files are the layout authority. The PDF is used only to recover chord
positions. This version prints progress and supports page ranges so you can test
small batches before OCR'ing the entire PDF.

Examples:

  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf --pages 8-20
  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf --hymns 1-40
  python3 scripts/build_himnos_acordes_from_pdf.py ~/Downloads/ACORDES\ HIMNOS.pdf --zoom 1.5

Requirements:

  brew install tesseract
  python3 -m pip install pymupdf
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
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

HEADING_RE = re.compile(r"^\s*(\d{1,3})\.\s+(.+?)\s*$")
TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
CHORD_TOKEN_RE = re.compile(r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$")
CHORD_SCAN_RE = re.compile(r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?")
SECTION_WORDS = {"coro", "chorus", "estrofa", "verse"}
BAD_SINGLE_CHORD_WORDS = {"A", "Y"}


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
    def y(self) -> int:
        return min(w.y for w in self.words) if self.words else 0


@dataclass
class SourceLine:
    hymn_number: int
    page_number: int
    chords: list[tuple[int, str]]
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
            start, end = int(a), int(b)
            selected.update(range(start, end + 1))
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
    if not text or text.lower().rstrip(":") in SECTION_WORDS:
        return False
    pieces = chord_pieces_from_line_text(text)
    if not pieces:
        return False
    if len(pieces) == 1 and pieces[0] in BAD_SINGLE_CHORD_WORDS:
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
            step = max(1, word.w // len(pieces))
            for i, piece in enumerate(pieces):
                out.append((word.x + i * step, piece))
    return out


def render_page_to_png(doc: fitz.Document, page_index: int, out_path: Path, zoom: float) -> None:
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(out_path)


def tesseract_tsv(image_path: Path, lang: str) -> list[OcrWord]:
    cmd = ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", lang, "tsv"]
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SystemExit("Missing tesseract. Install with: brew install tesseract") from exc
    except subprocess.CalledProcessError:
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
        if conf < 15:
            continue
        words.append(OcrWord(text, int(row.get("left", 0)), int(row.get("top", 0)), int(row.get("width", 0)), int(row.get("height", 0)), conf))
    return words


def group_words_into_lines(words: list[OcrWord]) -> list[OcrLine]:
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
    return sorted([OcrLine(sorted(g, key=lambda w: w.x)) for g in groups], key=lambda l: l.y)


def detect_hymn_heading(lines: list[OcrLine]) -> int | None:
    for line in lines[:10]:
        m = HEADING_RE.match(clean_spaces(line.text))
        if m:
            return int(m.group(1))
    return None


def pair_chords_to_lyrics(hymn_number: int, page_number: int, lines: list[OcrLine]) -> list[SourceLine]:
    source_lines: list[SourceLine] = []
    pending_chords: list[tuple[int, str]] = []
    for line in lines:
        text = clean_spaces(line.text)
        if not text or text.upper() == "INDICE" or HEADING_RE.match(text):
            continue
        if text.lower().rstrip(":") in SECTION_WORDS:
            continue
        if is_chord_line(line):
            pending_chords.extend(chords_from_ocr_line(line))
            continue
        source_lines.append(SourceLine(hymn_number, page_number, pending_chords, text))
        pending_chords = []
    return source_lines


def extract_pdf_sources(pdf_path: Path, page_numbers: set[int] | None, hymn_filter: set[int] | None, zoom: float, lang: str) -> dict[int, list[SourceLine]]:
    doc = fitz.open(pdf_path)
    sources: dict[int, list[SourceLine]] = {}
    total_pages = len(doc)
    pages = sorted(page_numbers or set(range(1, total_pages + 1)))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for count, page_number in enumerate(pages, 1):
            page_index = page_number - 1
            print(f"OCR page {page_number}/{total_pages} ({count}/{len(pages)})...", flush=True)
            image_path = tmp_dir / f"page-{page_number:03d}.png"
            render_page_to_png(doc, page_index, image_path, zoom)
            words = tesseract_tsv(image_path, lang)
            lines = group_words_into_lines(words)
            hymn_number = detect_hymn_heading(lines)
            if hymn_number is None:
                continue
            if hymn_filter is not None and hymn_number not in hymn_filter:
                continue
            source_lines = pair_chords_to_lyrics(hymn_number, page_number, lines)
            if source_lines:
                print(f"  found hymn {hymn_number}: {len(source_lines)} lyric lines", flush=True)
                sources.setdefault(hymn_number, []).extend(source_lines)
    return sources


def insert_chords_by_x(chords: list[tuple[int, str]], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric
    words = lyric.split()
    if len(words) <= 1:
        return "".join(f"[{ch}]" for _x, ch in chords) + lyric
    chords = sorted(chords, key=lambda item: item[0])
    min_x = min(x for x, _ in chords)
    max_x = max(x for x, _ in chords)
    slots: dict[int, list[str]] = {}
    for x, ch in chords:
        idx = 0 if max_x == min_x else round(((x - min_x) / (max_x - min_x)) * (len(words) - 1))
        slots.setdefault(idx, []).append(ch)
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
                out.append(insert_chords_by_x(src.chords, line))
            else:
                unchorded += 1
                out.append(line)
        else:
            unchorded += 1
            out.append(line)
    return "\n".join(out).rstrip() + "\n", {"matched": matched, "chorded": chorded, "unchorded": unchorded}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ChordPro hymns from clean TXT files and chorded PDF OCR.")
    parser.add_argument("pdf", help="Path to ACORDES HIMNOS.pdf")
    parser.add_argument("--pages", help="Page range to OCR, e.g. 8-20 or 8,9,10")
    parser.add_argument("--hymns", help="Only build selected hymn numbers, e.g. 1-40 or 517")
    parser.add_argument("--zoom", type=float, default=1.5, help="PDF render zoom. Default: 1.5. Use 2.0 if OCR is poor.")
    parser.add_argument("--lang", default="spa+eng", help="Tesseract language. Default: spa+eng; fallback to eng if unavailable.")
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

    print("Starting OCR extraction...", flush=True)
    sources = extract_pdf_sources(pdf_path, page_filter, hymn_filter, args.zoom, args.lang)
    print(f"OCR source hymns found: {len(sources)}", flush=True)

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
