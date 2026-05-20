#!/usr/bin/env python3
"""
Split the cleaned Himnos y Cánticos del Evangelio text into individual .txt files.

Run from repo root:
  python3 scripts/split_clean_text_to_txt.py

Input:
  fuentes/himnos-y-canticos-del-evangelio.clean.txt

Output:
  songs/txt/himnos/H001-a-casa-vete.txt
  songs/txt/coros/C001-a-los-cansados-convida.txt

This stage intentionally creates plain .txt files first, before .cho generation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

CLEAN_SOURCE = Path("fuentes/himnos-y-canticos-del-evangelio.clean.txt")
HYMNS_OUTPUT_DIR = Path("songs/txt/himnos")
CHORUSES_OUTPUT_DIR = Path("songs/txt/coros")
AUDIT_OUTPUT = Path("songs/txt/split-audit.txt")

HEADER_RE = re.compile(r"^(H|C)(\d{3})\s+(.+?)\s*$")


@dataclass
class Song:
    prefix: str
    number: int
    title: str
    body_lines: list[str]

    @property
    def song_id(self) -> str:
        return f"{self.prefix}{self.number:03d}"


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    ascii_text = re.sub(r"-+", "-", ascii_text).strip("-")
    return ascii_text or "sin-titulo"


def collapse_blank_lines(lines: list[str]) -> list[str]:
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


def parse_songs(text: str) -> list[Song]:
    songs: list[Song] = []
    current: Song | None = None

    for line in text.splitlines():
        if line.strip() == "# COROS":
            continue

        match = HEADER_RE.match(line)
        if match:
            if current is not None:
                current.body_lines = collapse_blank_lines(current.body_lines)
                songs.append(current)
            prefix = match.group(1)
            number = int(match.group(2))
            title = match.group(3).strip()
            current = Song(prefix=prefix, number=number, title=title, body_lines=[])
            continue

        if current is not None:
            current.body_lines.append(line)

    if current is not None:
        current.body_lines = collapse_blank_lines(current.body_lines)
        songs.append(current)

    return songs


def song_output_path(song: Song) -> Path:
    base_dir = HYMNS_OUTPUT_DIR if song.prefix == "H" else CHORUSES_OUTPUT_DIR
    return base_dir / f"{song.song_id}-{slugify(song.title)}.txt"


def render_song(song: Song) -> str:
    lines = [f"{song.song_id} {song.title}", "", *song.body_lines]
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    if not CLEAN_SOURCE.exists():
        raise SystemExit(f"Missing clean source file: {CLEAN_SOURCE}\nRun scripts/clean_hymnal_text.py first.")

    text = CLEAN_SOURCE.read_text(encoding="utf-8")
    songs = parse_songs(text)

    HYMNS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CHORUSES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Remove old generated .txt files so renamed titles do not leave stale files.
    for path in list(HYMNS_OUTPUT_DIR.glob("*.txt")) + list(CHORUSES_OUTPUT_DIR.glob("*.txt")):
        path.unlink()

    written_paths: list[Path] = []
    for song in songs:
        path = song_output_path(song)
        path.write_text(render_song(song), encoding="utf-8")
        written_paths.append(path)

    hymn_numbers = sorted(song.number for song in songs if song.prefix == "H")
    chorus_numbers = sorted(song.number for song in songs if song.prefix == "C")
    missing_hymns = [n for n in range(1, 518) if n not in hymn_numbers]
    missing_choruses = [n for n in range(1, 50) if n not in chorus_numbers]

    audit_lines = [
        f"Songs written: {len(written_paths)}",
        f"Hymns written: {len(hymn_numbers)}",
        f"Choruses written: {len(chorus_numbers)}",
        "",
        "Missing hymns:",
        ", ".join(f"H{n:03d}" for n in missing_hymns) if missing_hymns else "none",
        "",
        "Missing choruses:",
        ", ".join(f"C{n:03d}" for n in missing_choruses) if missing_choruses else "none",
        "",
        "Output directories:",
        str(HYMNS_OUTPUT_DIR),
        str(CHORUSES_OUTPUT_DIR),
    ]
    AUDIT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_OUTPUT.write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    print(f"Wrote {len(written_paths)} song txt files")
    print(f"Hymns written: {len(hymn_numbers)}")
    print(f"Choruses written: {len(chorus_numbers)}")
    print(f"Wrote {AUDIT_OUTPUT}")
    if missing_hymns:
        print("Missing hymns: " + ", ".join(f"H{n:03d}" for n in missing_hymns))
    if missing_choruses:
        print("Missing choruses: " + ", ".join(f"C{n:03d}" for n in missing_choruses))


if __name__ == "__main__":
    main()
