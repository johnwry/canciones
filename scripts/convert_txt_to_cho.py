#!/usr/bin/env python3
"""
Convert song TXT files into ChordPro .cho files.

This version does not depend on the current working directory.
It scans the repository for text files inside folders named:
  nuevos
  niños / ninos

Usage from anywhere inside the repo:
  python3 scripts/convert_txt_to_cho.py
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
TARGET_FOLDER_NAMES = {"nuevos", "niños", "ninos"}
TEXT_EXTENSIONS = {".txt", ".text", ".TXT"}

CHORD_RE = re.compile(
    r"^(?:\s*(?:[A-G](?:#|b)?(?:maj|min|m|sus|dim|aug|add)?\d*(?:/[A-G](?:#|b)?)?|N\.C\.|\||:|/)+\s*)+$",
    re.IGNORECASE,
)

TITLE_RE = re.compile(r"^\s*T[ií]tulo\s*:\s*(.+?)\s*$", re.IGNORECASE)
AUTHOR_RE = re.compile(r"^\s*Autor\s*:\s*(.+?)\s*$", re.IGNORECASE)
LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)\s*[.\-)]\s*(.+?)\s*$")


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalized_part(text: str) -> str:
    return strip_accents(text).lower()


def strip_accents_for_filename(text: str) -> str:
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "cancion"


def clean_title(raw_title: str, fallback_stem: str) -> str:
    title = raw_title.strip() or fallback_stem.strip()
    return re.sub(r"\s+", " ", title)


def filename_from_title(title: str, fallback_number: str | None = None) -> str:
    match = LEADING_NUMBER_RE.match(title)
    if match:
        number, rest = match.groups()
        return f"{int(number):02d}-{strip_accents_for_filename(rest)}.cho"
    if fallback_number:
        return f"{int(fallback_number):02d}-{strip_accents_for_filename(title)}.cho"
    return f"{strip_accents_for_filename(title)}.cho"


def is_chord_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    stripped = stripped.replace("//", " ").strip()
    return bool(stripped and CHORD_RE.match(stripped))


def split_chords(chord_line: str) -> list[tuple[int, str]]:
    clean = chord_line.replace("//", " ").rstrip("\n")
    chord_pattern = r"[A-G](?:#|b)?(?:maj|min|m|sus|dim|aug|add)?\d*(?:/[A-G](?:#|b)?)?|N\.C\."
    return [(m.start(), m.group(0)) for m in re.finditer(chord_pattern, clean, re.IGNORECASE)]


def merge_chords_with_lyrics(chord_line: str, lyric_line: str) -> str:
    chords = split_chords(chord_line)
    lyric = lyric_line.rstrip("\n")

    inserts: dict[int, list[str]] = {}
    for pos, chord in chords:
        idx = min(pos, len(lyric))
        inserts.setdefault(idx, []).append(chord)

    out: list[str] = []
    for i, ch in enumerate(lyric):
        if i in inserts:
            out.extend(f"[{chord}]" for chord in inserts[i])
        out.append(ch)

    if len(lyric) in inserts:
        out.extend(f"[{chord}]" for chord in inserts[len(lyric)])

    return "".join(out).rstrip()


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="replace")


def convert_text(content: str, fallback_stem: str) -> tuple[str, str]:
    lines = content.replace("\ufeff", "").splitlines()
    title = ""
    author = ""
    body: list[str] = []

    for line in lines:
        title_match = TITLE_RE.match(line)
        author_match = AUTHOR_RE.match(line)

        if title_match:
            title = clean_title(title_match.group(1), fallback_stem)
            continue

        if author_match:
            author = author_match.group(1).strip()
            continue

        body.append(line.rstrip())

    if not title:
        title = clean_title(fallback_stem, fallback_stem)

    output: list[str] = [f"{{title: {title}}}"]
    if author:
        output.append(f"{{artist: {author}}}")
    output.append("")

    i = 0
    while i < len(body):
        line = body[i]

        if is_chord_line(line) and i + 1 < len(body) and body[i + 1].strip():
            output.append(merge_chords_with_lyrics(line, body[i + 1]))
            i += 2
            continue

        output.append(line.rstrip())
        i += 1

    return title, "\n".join(output).rstrip() + "\n"


def target_group_for_path(path: Path) -> str | None:
    for part in path.parts:
        normalized = normalized_part(part)
        if normalized in TARGET_FOLDER_NAMES:
            return "niños" if normalized == "ninos" else part
    return None


def discover_source_files() -> list[Path]:
    files: list[Path] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if "cho" in [normalized_part(part) for part in path.parts]:
            continue
        if path.suffix not in TEXT_EXTENSIONS:
            continue
        if target_group_for_path(path.relative_to(REPO_ROOT)) is None:
            continue
        files.append(path)

    return sorted(files)


def output_path_for_source(source_path: Path, title: str) -> Path:
    rel = source_path.relative_to(REPO_ROOT)
    group = target_group_for_path(rel) or "nuevos"
    number_match = re.match(r"^\s*(\d+)", source_path.stem)
    output_name = filename_from_title(title, number_match.group(1) if number_match else None)
    return REPO_ROOT / "cho" / group / output_name


def main() -> None:
    source_files = discover_source_files()

    if not source_files:
        print("No source .txt files found inside folders named nuevos or niños/ninos.")
        print(f"Repo root detected as: {REPO_ROOT}")
        print("Tip: run this to inspect your actual paths:")
        print("  find . -maxdepth 5 -type f | sort")
        return

    total = 0

    for source_path in source_files:
        title, cho_text = convert_text(read_text_file(source_path), source_path.stem)
        output_path = output_path_for_source(source_path, title)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(cho_text, encoding="utf-8")
        print(f"WROTE {output_path.relative_to(REPO_ROOT)}")
        total += 1

    print(f"Converted {total} song(s).")


if __name__ == "__main__":
    main()
