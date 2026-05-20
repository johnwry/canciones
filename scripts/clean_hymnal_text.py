#!/usr/bin/env python3
"""
Clean the raw OCR/text extraction of Himnos y Cánticos del Evangelio.

This script does NOT create .cho files yet. It creates an intermediate corrected
plain-text file that can be reviewed and edited before ChordPro conversion.

Run from repo root:
  python3 scripts/clean_hymnal_text.py

Input:
  428925442-Himnos-y-Canticos-Del-Evangelio.txt

Output:
  fuentes/himnos-y-canticos-del-evangelio.clean.txt
"""

from __future__ import annotations

import re
from pathlib import Path

RAW_SOURCE = Path("428925442-Himnos-y-Canticos-Del-Evangelio.txt")
CLEAN_OUTPUT = Path("fuentes/himnos-y-canticos-del-evangelio.clean.txt")

INDEX_LINE_RE = re.compile(r"^\s*(INDICE|ÍNDICE|COROS)\s*$", re.IGNORECASE)
RANGE_LINE_RE = re.compile(r"^\s*\d{3}\s*[-–—]{3}\s*\d{3}\s*$")
SECTION_INDEX_RE = re.compile(r"^\s*(INDICE|ÍNDICE|COROS)\s*\(.*?\)\s*$", re.IGNORECASE)
HIDDEN_LABEL_RE = re.compile(r"\s+(INDICE|ÍNDICE|COROS)\s*$", re.IGNORECASE)
PAGE_RANGE_AT_END_RE = re.compile(r"\s+\d{3}\s*[-–—]{3}\s*\d{3}\s*$")

HYMN_HEADER_RE = re.compile(r"^\s*(\d{3})\s+(.+?)\s*$")
CHORUS_HEADER_RE = re.compile(r"^\s*(\d{3})-\s+(.+?)\s*$")

# The real hymn body begins here. Earlier 001 entries are just index entries.
REAL_FIRST_HYMN_RE = re.compile(r"^\s*001\s+A\s+CASA\s+VETE\s*$", re.IGNORECASE)

TITLE_FIXES = {
    "CONTAD EN ALTA VOZ": "CANTAD EN ALTA VOZ",
}


def normalize_line(line: str) -> str:
    line = line.rstrip()
    line = line.replace("\u00a0", " ")
    line = line.replace("…", "...")
    line = line.replace("“", '"').replace("”", '"')
    line = line.replace("‘", "'").replace("’", "'")
    line = re.sub(r"[ \t]+$", "", line)
    return line


def is_artifact_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        INDEX_LINE_RE.match(stripped)
        or RANGE_LINE_RE.match(stripped)
        or SECTION_INDEX_RE.match(stripped)
    )


def remove_inline_artifacts(line: str) -> str:
    line = HIDDEN_LABEL_RE.sub("", line)
    line = PAGE_RANGE_AT_END_RE.sub("", line)
    return line.rstrip()


def title_case_for_display(title: str) -> str:
    title = re.sub(r"\s+", " ", title.strip())
    return TITLE_FIXES.get(title.upper(), title)


def append_blank(cleaned: list[str]) -> None:
    if cleaned and cleaned[-1] != "":
        cleaned.append("")


def main() -> None:
    if not RAW_SOURCE.exists():
        raise SystemExit(f"Missing source file: {RAW_SOURCE}")

    raw_lines = RAW_SOURCE.read_text(encoding="utf-8", errors="replace").splitlines()

    cleaned: list[str] = []
    in_hymn_body = False
    in_chorus_body = False
    seen_hymn_517 = False

    for raw in raw_lines:
        line = normalize_line(raw)
        line = remove_inline_artifacts(line)
        stripped = line.strip()

        # Skip everything until the actual hymn body begins.
        if not in_hymn_body and not in_chorus_body:
            if REAL_FIRST_HYMN_RE.match(stripped):
                cleaned.append("H001 A CASA VETE")
                cleaned.append("")
                in_hymn_body = True
            continue

        if not stripped:
            append_blank(cleaned)
            continue

        if is_artifact_line(line):
            continue

        chorus_match = CHORUS_HEADER_RE.match(line)
        hymn_match = HYMN_HEADER_RE.match(line)

        # After hymn 517, the next 001- style header starts the chorus body.
        if seen_hymn_517 and chorus_match:
            if not in_chorus_body:
                append_blank(cleaned)
                cleaned.append("# COROS")
                cleaned.append("")
            in_hymn_body = False
            in_chorus_body = True
            number = int(chorus_match.group(1))
            title = title_case_for_display(chorus_match.group(2))
            append_blank(cleaned)
            cleaned.append(f"C{number:03d} {title}")
            cleaned.append("")
            continue

        if in_chorus_body and chorus_match:
            number = int(chorus_match.group(1))
            title = title_case_for_display(chorus_match.group(2))
            append_blank(cleaned)
            cleaned.append(f"C{number:03d} {title}")
            cleaned.append("")
            continue

        if in_hymn_body and hymn_match:
            number = int(hymn_match.group(1))
            title = title_case_for_display(hymn_match.group(2))
            append_blank(cleaned)
            cleaned.append(f"H{number:03d} {title}")
            cleaned.append("")
            if number == 517:
                seen_hymn_517 = True
            continue

        lyric = re.sub(r"^\s+", "", line)
        cleaned.append(lyric)

    text = "\n".join(cleaned).strip() + "\n"
    CLEAN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CLEAN_OUTPUT.write_text(text, encoding="utf-8")

    h_count = len(re.findall(r"^H\d{3}\s", text, flags=re.MULTILINE))
    c_count = len(re.findall(r"^C\d{3}\s", text, flags=re.MULTILINE))

    print(f"Wrote {CLEAN_OUTPUT}")
    print(f"Detected hymns: {h_count}")
    print(f"Detected choruses: {c_count}")


if __name__ == "__main__":
    main()
