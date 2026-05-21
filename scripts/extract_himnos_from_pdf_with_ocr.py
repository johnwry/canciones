#!/usr/bin/env python3
"""
Extract each hymn from ACORDES HIMNOS.pdf into individual TXT files.

This replaces the earlier plain-text extraction when the PDF page is image-only.
It first tries embedded PDF text. If a hymn page only extracts junk like INDICE,
it falls back to OCR for that page.

Output:

  songs/txt/himnos-from-pdf

Run from repository root:

  python3 scripts/extract_himnos_from_pdf_with_ocr.py ~/Downloads/ACORDES\ HIMNOS.pdf

Optional small test:

  python3 scripts/extract_himnos_from_pdf_with_ocr.py ~/Downloads/ACORDES\ HIMNOS.pdf --pages 12
  python3 scripts/extract_himnos_from_pdf_with_ocr.py ~/Downloads/ACORDES\ HIMNOS.pdf --pages 8-20

Requirements:

  python3 -m pip install pymupdf
  brew install tesseract

Optional Spanish OCR:

  brew install tesseract-lang
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
import shutil
import subprocess
import tempfile

try:
    import fitz  # PyMuPDF
except ImportError as exc:
    raise SystemExit("Missing dependency: pymupdf. Install with: python3 -m pip install pymupdf") from exc

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "songs" / "txt" / "himnos-from-pdf"

HEADING_RE = re.compile(r"(?m)^\s*(\d{1,3})\.\s+(.+?)\s*(?:INDICE)?\s*$")
PAGE_MARKER_RE = re.compile(r"^={3,}\s*PAGE\s+\d+\s*={3,}$", re.IGNORECASE)


def clean_line(s: str) -> str:
    return (
        s.replace("\u00a0", " ")
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("￾", "")
        .rstrip()
    )


def sanitize_filename(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|]', "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:120]


def parse_range(spec: str | None, maximum: int) -> set[int] | None:
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
    return {n for n in selected if 1 <= n <= maximum}


def useful_text(text: str) -> bool:
    lines = [clean_line(l).strip() for l in text.splitlines()]
    useful = [l for l in lines if l and l.upper() != "INDICE" and not PAGE_MARKER_RE.match(l)]
    # A heading alone is not enough. We need actual hymn content.
    non_heading = [l for l in useful if not HEADING_RE.match(l)]
    return len(non_heading) >= 4


def render_page(doc: fitz.Document, page_index: int, image_path: Path, zoom: float) -> None:
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(image_path)


def ocr_page(image_path: Path, lang: str) -> str:
    cmd = ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", lang]
    try:
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SystemExit("Missing tesseract. Install with: brew install tesseract") from exc
    except subprocess.CalledProcessError:
        cmd = ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", "eng"]
        result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout


def extract_page_text(doc: fitz.Document, page_number: int, tmp_dir: Path, zoom: float, lang: str) -> tuple[str, str]:
    page = doc[page_number - 1]
    embedded = page.get_text("text") or ""
    if useful_text(embedded):
        return embedded, "text"

    image_path = tmp_dir / f"page-{page_number:03d}.png"
    render_page(doc, page_number - 1, image_path, zoom)
    ocr = ocr_page(image_path, lang)
    if useful_text(ocr):
        return ocr, "ocr"
    return embedded if embedded.strip() else ocr, "empty"


def write_hymn_file(num: int, title: str, body: str) -> Path:
    safe = sanitize_filename(title)
    out_path = OUT_DIR / f"{num:03d} - {safe}.txt"

    lines: list[str] = []
    prev_blank = False
    for raw_line in body.splitlines():
        line = clean_line(raw_line).strip()
        if PAGE_MARKER_RE.match(line):
            continue
        if line.upper() == "INDICE":
            continue
        # Remove duplicated heading inside body.
        if HEADING_RE.match(line):
            continue
        if not line:
            if not prev_blank:
                lines.append("")
            prev_blank = True
            continue
        prev_blank = False
        lines.append(line)

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()

    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract hymn TXT files from PDF with OCR fallback.")
    parser.add_argument("pdf", help="Path to ACORDES HIMNOS.pdf")
    parser.add_argument("--pages", help="Optional page range, e.g. 12 or 8-20")
    parser.add_argument("--zoom", type=float, default=2.0, help="OCR render zoom. Default: 2.0")
    parser.add_argument("--lang", default="spa+eng", help="Tesseract language. Default: spa+eng")
    parser.add_argument("--keep", action="store_true", help="Do not delete existing himnos-from-pdf files first")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    if OUT_DIR.exists() and not args.keep:
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    page_filter = parse_range(args.pages, len(doc))
    pages = sorted(page_filter or set(range(1, len(doc) + 1)))

    created = 0
    text_pages = 0
    ocr_pages = 0
    empty_pages = 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for idx, page_number in enumerate(pages, 1):
            print(f"Extracting page {page_number}/{len(doc)} ({idx}/{len(pages)})...", flush=True)
            page_text, mode = extract_page_text(doc, page_number, tmp_dir, args.zoom, args.lang)
            if mode == "text":
                text_pages += 1
            elif mode == "ocr":
                ocr_pages += 1
            else:
                empty_pages += 1

            headings = list(HEADING_RE.finditer(page_text))
            if not headings:
                continue

            for h_idx, heading in enumerate(headings):
                num = int(heading.group(1))
                title = heading.group(2).strip()
                start = heading.end()
                end = headings[h_idx + 1].start() if h_idx + 1 < len(headings) else len(page_text)
                body = page_text[start:end]
                out_path = write_hymn_file(num, title, body)
                created += 1
                print(f"  wrote {out_path.name} via {mode}", flush=True)

    print(f"Created/updated hymn TXT files: {created}")
    print(f"Pages from embedded text: {text_pages}")
    print(f"Pages from OCR fallback: {ocr_pages}")
    print(f"Pages still empty/unusable: {empty_pages}")


if __name__ == "__main__":
    main()
