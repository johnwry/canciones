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
SECTION_INDEX_RE = re.compile(r"^\s*(INDICE|ÍNDICE)\s*\(.*?\)\s*$", re.IGNORECASE)
HIDDEN_RANGE_RE = re.compile(r"\s+(INDICE|ÍNDICE|COROS)\s*$", re.IGNORECASE)
PAGE_RANGE_AT_END_RE = re.compile(r"\s+\d{3}\s*[-–—]{3}\s*\d{3}\s*$")

HYMN_HEADER_RE = re.compile(r"^\s*(\d{3})\s+(.+?)\s*$")
CHORUS_HEADER_RE = re.compile(r"^\s*(\d{3})-\s+(.+?)\s*$")

# Common OCR/title fixes observed in the source.
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
    if INDEX_LINE_RE.match(stripped):
        return True
    if RANGE_LINE_RE.match(stripped):
        return True
    if SECTION_INDEX_RE.match(stripped):
        return True
    return False


def remove_inline_artifacts(line: str) -> str:
    line = HIDDEN_RANGE_RE.sub("", line)
    line = PAGE_RANGE_AT_END_RE.sub("", line)
    return line.rstrip()


def title_case_for_display(title: str) -> str:
    title = re.sub(r"\s+", " ", title.strip())
    fixed = TITLE_FIXES.get(title.upper())
    if fixed:
        title = fixed
    return title


def main() -> None:
    if not RAW_SOURCE.exists():
        raise SystemExit(f"Missing source file: {RAW_SOURCE}")

    raw_lines = RAW_SOURCE.read_text(encoding="utf-8", errors="replace").splitlines()

    cleaned: list[str] = []
    in_song_body = False
    in_chorus_section = False
    previous_blank = False

    for raw in raw_lines:
        line = normalize_line(raw)
        stripped = line.strip()

        if stripped == "COROS (001---049)":
            in_chorus_section = True
            in_song_body = False
            cleaned.append("")
            cleaned.append("# COROS")
            cleaned.append("")
            previous_blank = True
            continue

        if is_artifact_line(line):
            continue

        line = remove_inline_artifacts(line)
        stripped = line.strip()

        if not stripped:
            if not previous_blank:
                cleaned.append("")
                previous_blank = True
            continue

        chorus_match = CHORUS_HEADER_RE.match(line)
        hymn_match = HYMN_HEADER_RE.match(line)

        if in_chorus_section and chorus_match:
            number = int(chorus_match.group(1))
            title = title_case_for_display(chorus_match.group(2))
            cleaned.append("")
            cleaned.append(f"C{number:03d} {title}")
            cleaned.append("")
            in_song_body = True
            previous_blank = True
            continue

        if not in_chorus_section and hymn_match:
            number = int(hymn_match.group(1))
            # Ignore index entries before actual hymn 001 starts.
            if not in_song_body and number != 1:
                continue
            title = title_case_for_display(hymn_match.group(2))
            cleaned.append("")
            cleaned.append(f"H{number:03d} {title}")
            cleaned.append("")
            in_song_body = True
            previous_blank = True
            continue

        if not in_song_body:
            continue

        # Preserve lyric indentation lightly, but remove excessive PDF centering.
        lyric = re.sub(r"^\s{1,}", "", line)
        cleaned.append(lyric)
        previous_blank = False

    text = "\n".join(cleaned).strip() + "\n"
    CLEAN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CLEAN_OUTPUT.write_text(text, encoding="utf-8")

    print(f"Wrote {CLEAN_OUTPUT}")


if __name__ == "__main__":
    main()
