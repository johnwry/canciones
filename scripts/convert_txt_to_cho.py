#!/usr/bin/env python3
"""
Convert song .txt files into ChordPro .cho files.

Default input folders:
  canciones/txt/nuevos/
  canciones/txt/niños/

Default output folders:
  canciones/cho/nuevos/
  canciones/cho/niños/

Usage from the repository root:
  python3 scripts/convert_txt_to_cho.py
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

INPUT_FOLDERS = [
    Path("canciones/txt/nuevos"),
    Path("canciones/txt/niños"),
]

CHORD_RE = re.compile(
    r"^(?:\s*(?:[A-G](?:#|b)?(?:maj|min|m|sus|dim|aug|add)?\d*(?:/[A-G](?:#|b)?)?|N\.C\.|\||:|/)+\s*)+$",
    re.IGNORECASE,
)

TITLE_RE = re.compile(r"^\s*T[ií]tulo\s*:\s*(.+?)\s*$", re.IGNORECASE)
AUTHOR_RE = re.compile(r"^\s*Autor\s*:\s*(.+?)\s*$", re.IGNORECASE)
LEADING_NUMBER_RE = re.compile(r"^\s*(\d+)\s*[.\-)]\s*(.+?)\s*$")


def strip_accents_for_filename(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "cancion"


def clean_title(raw_title: str, fallback_stem: str) -> str:
    title = raw_title.strip() or fallback_stem.strip()
    title = re.sub(r"\s+", " ", title)
    return title


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
    # Existing source files often prefix chord-only lines with // as a visual marker.
    stripped = stripped.replace("//", " ").strip()
    return bool(stripped and CHORD_RE.match(stripped))


def split_chords(chord_line: str) -> list[tuple[int, str]]:
    clean = chord_line.replace("//", " ").rstrip("\n")
    return [(m.start(), m.group(0)) for m in re.finditer(r"[A-G](?:#|b)?(?:maj|min|m|sus|dim|aug|add)?\d*(?:/[A-G](?:#|b)?)?|N\.C\.", clean, re.IGNORECASE)]


def merge_chords_with_lyrics(chord_line: str, lyric_line: str) -> str:
    chords = split_chords(chord_line)
    lyric = lyric_line.rstrip("\n")
    if not chords:
        return lyric

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


def normalize_repeats(line: str) -> str:
    # Keep repeated-slash notation readable in ChordPro comments rather than losing it.
    stripped = line.strip()
    if stripped.startswith("///") or stripped.startswith("//") or stripped.startswith("/"):
        return line.rstrip()
    return line.rstrip()


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
        if is_chord_line(line):
            chords_only = " ".join(chord for _, chord in split_chords(line))
            output.append(f"{{comment: {chords_only}}}" if chords_only else "")
            i += 1
            continue
        output.append(normalize_repeats(line))
        i += 1

    text = "\n".join(output).rstrip() + "\n"
    return title, text


def convert_folder(input_folder: Path) -> int:
    if not input_folder.exists():
        print(f"SKIP missing folder: {input_folder}")
        return 0

    relative_parts = input_folder.parts
    try:
        txt_index = relative_parts.index("txt")
        subfolder = Path(*relative_parts[txt_index + 1 :])
    except ValueError:
        subfolder = Path(input_folder.name)

    output_folder = Path("canciones/cho") / subfolder
    output_folder.mkdir(parents=True, exist_ok=True)

    count = 0
    for txt_path in sorted(input_folder.glob("*.txt")):
        title, cho_text = convert_text(txt_path.read_text(encoding="utf-8"), txt_path.stem)
        number_match = re.match(r"^\s*(\d+)", txt_path.stem)
        output_name = filename_from_title(title, number_match.group(1) if number_match else None)
        output_path = output_folder / output_name
        output_path.write_text(cho_text, encoding="utf-8")
        print(f"WROTE {output_path}")
        count += 1
    return count


def main() -> None:
    total = 0
    for folder in INPUT_FOLDERS:
        total += convert_folder(folder)
    print(f"Converted {total} song(s).")


if __name__ == "__main__":
    main()
