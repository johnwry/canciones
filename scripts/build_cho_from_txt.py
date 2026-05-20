#!/usr/bin/env python3
"""
Generate .cho files from the individual plain .txt hymn/coro files.

Run from repo root:
  python3 scripts/build_cho_from_txt.py

Input:
  songs/txt/himnos/*.txt
  songs/txt/coros/*.txt

Output:
  songs/cho/himnos/*.cho
  songs/cho/coros/*.cho

This script assumes the first line of each .txt file is:
  H001 Title
or:
  C001 Title
"""

from __future__ import annotations

import re
from pathlib import Path

TXT_HYMNS_DIR = Path("songs/txt/himnos")
TXT_CHORUSES_DIR = Path("songs/txt/coros")
CHO_HYMNS_DIR = Path("songs/cho/himnos")
CHO_CHORUSES_DIR = Path("songs/cho/coros")
AUDIT_OUTPUT = Path("songs/cho/cho-audit.txt")

HEADER_RE = re.compile(r"^(H|C)(\d{3})\s+(.+?)\s*$")

BOOK_NAME = "Himnos y Cánticos del Evangelio"
ARTIST_NAME = "Himnos y Cánticos del Evangelio"


def normalize_body_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    previous_blank = False

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            if not previous_blank:
                output.append("")
                previous_blank = True
            continue
        output.append(stripped)
        previous_blank = False

    while output and output[0] == "":
        output.pop(0)
    while output and output[-1] == "":
        output.pop()

    return output


def parse_txt_file(path: Path) -> tuple[str, int, str, list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"Empty file: {path}")

    match = HEADER_RE.match(lines[0].strip())
    if not match:
        raise ValueError(f"Invalid first line in {path}: {lines[0]!r}")

    prefix = match.group(1)
    number = int(match.group(2))
    title = match.group(3).strip()
    body = normalize_body_lines(lines[1:])

    return prefix, number, title, body


def render_cho(prefix: str, number: int, title: str, body: list[str]) -> str:
    song_id = f"{prefix}{number:03d}"
    song_type = "Himno" if prefix == "H" else "Coro"

    lines = [
        f"{{title: {title}}}",
        f"{{artist: {ARTIST_NAME}}}",
        f"{{book: {BOOK_NAME}}}",
        f"{{number: {song_id}}}",
        f"{{song_id: {song_id}}}",
        f"{{type: {song_type}}}",
        "",
        "{verse: 1}",
        *body,
    ]

    return "\n".join(lines).strip() + "\n"


def output_path_for(txt_path: Path, prefix: str) -> Path:
    output_dir = CHO_HYMNS_DIR if prefix == "H" else CHO_CHORUSES_DIR
    return output_dir / txt_path.with_suffix(".cho").name


def main() -> None:
    if not TXT_HYMNS_DIR.exists() or not TXT_CHORUSES_DIR.exists():
        raise SystemExit(
            "Missing txt input directories. Run scripts/split_clean_text_to_txt.py first."
        )

    CHO_HYMNS_DIR.mkdir(parents=True, exist_ok=True)
    CHO_CHORUSES_DIR.mkdir(parents=True, exist_ok=True)

    # Remove old generated .cho files so renamed titles do not leave stale files.
    for path in list(CHO_HYMNS_DIR.glob("*.cho")) + list(CHO_CHORUSES_DIR.glob("*.cho")):
        path.unlink()

    txt_files = sorted(TXT_HYMNS_DIR.glob("*.txt")) + sorted(TXT_CHORUSES_DIR.glob("*.txt"))

    written: list[Path] = []
    errors: list[str] = []
    hymn_numbers: list[int] = []
    chorus_numbers: list[int] = []

    for txt_path in txt_files:
        try:
            prefix, number, title, body = parse_txt_file(txt_path)
            cho_text = render_cho(prefix, number, title, body)
            cho_path = output_path_for(txt_path, prefix)
            cho_path.write_text(cho_text, encoding="utf-8")
            written.append(cho_path)
            if prefix == "H":
                hymn_numbers.append(number)
            else:
                chorus_numbers.append(number)
        except Exception as exc:
            errors.append(f"{txt_path}: {exc}")

    missing_hymns = [n for n in range(1, 518) if n not in hymn_numbers]
    missing_choruses = [n for n in range(1, 50) if n not in chorus_numbers]

    audit_lines = [
        f"CHO files written: {len(written)}",
        f"Hymns written: {len(hymn_numbers)}",
        f"Choruses written: {len(chorus_numbers)}",
        "",
        "Missing hymns:",
        ", ".join(f"H{n:03d}" for n in missing_hymns) if missing_hymns else "none",
        "",
        "Missing choruses:",
        ", ".join(f"C{n:03d}" for n in missing_choruses) if missing_choruses else "none",
        "",
        "Errors:",
        *(errors or ["none"]),
        "",
        "Output directories:",
        str(CHO_HYMNS_DIR),
        str(CHO_CHORUSES_DIR),
    ]

    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    print(f"CHO files written: {len(written)}")
    print(f"Hymns written: {len(hymn_numbers)}")
    print(f"Choruses written: {len(chorus_numbers)}")
    print(f"Wrote {AUDIT_OUTPUT}")
    if missing_hymns:
        print("Missing hymns: " + ", ".join(f"H{n:03d}" for n in missing_hymns))
    if missing_choruses:
        print("Missing choruses: " + ", ".join(f"C{n:03d}" for n in missing_choruses))
    if errors:
        print("Errors:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
