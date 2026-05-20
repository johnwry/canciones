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
  fuentes/himnos-y-canticos-del-evangelio.audit.txt
"""

from __future__ import annotations

import re
from pathlib import Path

RAW_SOURCE = Path("428925442-Himnos-y-Canticos-Del-Evangelio.txt")
CLEAN_OUTPUT = Path("fuentes/himnos-y-canticos-del-evangelio.clean.txt")
AUDIT_OUTPUT = Path("fuentes/himnos-y-canticos-del-evangelio.audit.txt")

INDEX_LINE_RE = re.compile(r"^\s*(INDICE|ÍNDICE|COROS)\s*$", re.IGNORECASE)
RANGE_LINE_RE = re.compile(r"^\s*\d{3}\s*[-–—]{3}\s*\d{3}\s*$")
SECTION_INDEX_RE = re.compile(r"^\s*(INDICE|ÍNDICE|COROS)\s*\(.*?\)\s*$", re.IGNORECASE)
HIDDEN_LABEL_RE = re.compile(r"\s+(INDICE|ÍNDICE|COROS)\s*$", re.IGNORECASE)
PAGE_RANGE_AT_END_RE = re.compile(r"\s+\d{3}\s*[-–—]{3}\s*\d{3}\s*$")

# Broad header candidate: catches both hymn headers like "126 TITLE"
# and chorus headers like "001- TITLE".
HEADER_CANDIDATE_RE = re.compile(r"^\s*(\d{3})(?:\s*[-–—]\s*|\s+)(.*?)\s*$")

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


def find_next_nonblank_clean_line(lines: list[str], start_index: int) -> str:
    """Used when a title is wrapped and the header line has little/no title."""
    for j in range(start_index + 1, min(start_index + 6, len(lines))):
        candidate = remove_inline_artifacts(normalize_line(lines[j])).strip()
        if not candidate:
            continue
        if is_artifact_line(candidate):
            continue
        return candidate
    return "SIN TÍTULO"


def main() -> None:
    if not RAW_SOURCE.exists():
        raise SystemExit(f"Missing source file: {RAW_SOURCE}")

    raw_lines = RAW_SOURCE.read_text(encoding="utf-8", errors="replace").splitlines()

    cleaned: list[str] = []
    audit: list[str] = []

    in_hymn_body = False
    in_chorus_body = False
    expected_hymn = 1
    expected_chorus = 1
    detected_hymns: list[int] = []
    detected_choruses: list[int] = []

    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i]
        line = normalize_line(raw)
        line = remove_inline_artifacts(line)
        stripped = line.strip()

        # Skip everything until the actual hymn body begins.
        if not in_hymn_body and not in_chorus_body:
            if REAL_FIRST_HYMN_RE.match(stripped):
                cleaned.append("H001 A CASA VETE")
                cleaned.append("")
                in_hymn_body = True
                detected_hymns.append(1)
                expected_hymn = 2
            i += 1
            continue

        if not stripped:
            append_blank(cleaned)
            i += 1
            continue

        if is_artifact_line(line):
            i += 1
            continue

        match = HEADER_CANDIDATE_RE.match(line)

        if in_hymn_body and match:
            number = int(match.group(1))
            title = title_case_for_display(match.group(2))

            if number == expected_hymn:
                if not title:
                    title = title_case_for_display(find_next_nonblank_clean_line(raw_lines, i))
                    audit.append(f"H{number:03d}: title was recovered from following line")
                append_blank(cleaned)
                cleaned.append(f"H{number:03d} {title}")
                cleaned.append("")
                detected_hymns.append(number)
                expected_hymn += 1
                if number == 517:
                    in_hymn_body = False
                i += 1
                continue

            # If OCR skipped a header pattern, record jumps but do not accept out-of-sequence headers.
            if number > expected_hymn and number <= 517:
                audit.append(
                    f"Possible missed hymn header before source line {i + 1}: expected H{expected_hymn:03d}, saw {number:03d}"
                )

        # Chorus body begins after H517. Actual chorus headers are 001- ...
        if not in_hymn_body and match:
            number = int(match.group(1))
            title = title_case_for_display(match.group(2))
            if number == expected_chorus:
                if not in_chorus_body:
                    append_blank(cleaned)
                    cleaned.append("# COROS")
                    cleaned.append("")
                    in_chorus_body = True
                if not title:
                    title = title_case_for_display(find_next_nonblank_clean_line(raw_lines, i))
                    audit.append(f"C{number:03d}: title was recovered from following line")
                append_blank(cleaned)
                cleaned.append(f"C{number:03d} {title}")
                cleaned.append("")
                detected_choruses.append(number)
                expected_chorus += 1
                i += 1
                continue

        lyric = re.sub(r"^\s+", "", line)
        cleaned.append(lyric)
        i += 1

    text = "\n".join(cleaned).strip() + "\n"
    CLEAN_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    CLEAN_OUTPUT.write_text(text, encoding="utf-8")

    missing_hymns = [n for n in range(1, 518) if n not in detected_hymns]
    missing_choruses = [n for n in range(1, 50) if n not in detected_choruses]

    audit_text = []
    audit_text.append(f"Detected hymns: {len(detected_hymns)}")
    audit_text.append(f"Detected choruses: {len(detected_choruses)}")
    audit_text.append("")
    audit_text.append("Missing hymns:")
    audit_text.append(", ".join(f"H{n:03d}" for n in missing_hymns) if missing_hymns else "none")
    audit_text.append("")
    audit_text.append("Missing choruses:")
    audit_text.append(", ".join(f"C{n:03d}" for n in missing_choruses) if missing_choruses else "none")
    audit_text.append("")
    audit_text.append("Notes:")
    audit_text.extend(audit or ["none"])
    AUDIT_OUTPUT.write_text("\n".join(audit_text) + "\n", encoding="utf-8")

    print(f"Wrote {CLEAN_OUTPUT}")
    print(f"Wrote {AUDIT_OUTPUT}")
    print(f"Detected hymns: {len(detected_hymns)}")
    print(f"Detected choruses: {len(detected_choruses)}")
    if missing_hymns:
        print("Missing hymns: " + ", ".join(f"H{n:03d}" for n in missing_hymns))
    if missing_choruses:
        print("Missing choruses: " + ", ".join(f"C{n:03d}" for n in missing_choruses))


if __name__ == "__main__":
    main()
