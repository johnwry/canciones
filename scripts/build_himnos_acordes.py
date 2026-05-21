#!/usr/bin/env python3
"""
Build corrected ChordPro hymn files by combining:

  songs/txt/himnos        = clean lyric text
  songs/cho/himnos        = chord source files, even if chords are broken onto their own lines

Output:

  songs/cho/himnos-acordes

Run from the repository root:

  python3 scripts/build_himnos_acordes.py

This script is intentionally conservative. It never changes the original .txt or .cho files.
"""

from __future__ import annotations

from pathlib import Path
import difflib
import re
import shutil
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
TXT_DIR = ROOT / "songs" / "txt" / "himnos"
CHO_DIR = ROOT / "songs" / "cho" / "himnos"
OUT_DIR = ROOT / "songs" / "cho" / "himnos-acordes"

CHORD_RE = re.compile(
    r"^[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?$"
)
INLINE_CHORD_RE = re.compile(r"\[([^\]]+)\]")
TITLE_RE = re.compile(r"^\{title:\s*(.*?)\s*\}$", re.IGNORECASE)
COMMENT_RE = re.compile(r"^\{comment:\s*(.*?)\s*\}$", re.IGNORECASE)


def clean_spaces(s: str) -> str:
    return (
        s.replace("\u00a0", " ")
        .replace("\ufeff", "")
        .replace("￾", "")
        .replace("\u200b", "")
        .rstrip()
    )


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_for_match(s: str) -> str:
    s = INLINE_CHORD_RE.sub("", s)
    s = COMMENT_RE.sub("", s)
    s = re.sub(r"\{[^}]+\}", "", s)
    s = strip_accents(s).lower()
    s = re.sub(r"[^a-z0-9ñ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def is_chord_token(tok: str) -> bool:
    tok = tok.strip().strip(".")
    return bool(CHORD_RE.fullmatch(tok))


def split_chord_stream(tok: str) -> list[str] | None:
    """Split glued chord strings like C#7F#m or F#mG#m if possible."""
    tok = tok.strip().strip(".")
    if is_chord_token(tok):
        return [tok]

    out: list[str] = []
    i = 0
    # Greedy chord scanner.
    pattern = re.compile(r"[A-G](?:#|b)?(?:m|maj|min|dim|aug|sus|add|\+)?\d*(?:/[A-G](?:#|b)?)?")
    while i < len(tok):
        m = pattern.match(tok, i)
        if not m:
            return None
        out.append(m.group(0))
        i = m.end()
    return out or None


def chord_tokens_from_plain_line(line: str) -> list[str]:
    tokens: list[str] = []
    for raw in clean_spaces(line).split():
        pieces = split_chord_stream(raw)
        if not pieces:
            return []
        tokens.extend(pieces)
    return tokens


def is_plain_chord_line(line: str) -> bool:
    s = clean_spaces(line).strip()
    if not s:
        return False
    if s.lower().rstrip(":") in {"coro", "chorus", "estrofa", "verse"}:
        return False
    return bool(chord_tokens_from_plain_line(s))


def line_chords(line: str) -> list[str]:
    """Return chords from either [A] inline form or plain chord-only form."""
    inline = INLINE_CHORD_RE.findall(line)
    if inline:
        return [c.strip() for c in inline if c.strip()]
    return chord_tokens_from_plain_line(line)


def line_lyrics(line: str) -> str:
    line = INLINE_CHORD_RE.sub("", clean_spaces(line))
    return line.strip()


def title_from_cho(text: str, fallback: str) -> str:
    for line in text.splitlines():
        m = TITLE_RE.match(line.strip())
        if m:
            return m.group(1).strip()
    return fallback


def read_clean_lyrics(txt_path: Path) -> list[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines: list[str] = []
    for line in raw.splitlines():
        line = clean_spaces(line).strip()
        if not line:
            continue
        if TITLE_RE.match(line):
            continue
        if line.upper() == "INDICE":
            continue
        lines.append(line)
    return lines


def parse_chord_source(cho_path: Path) -> tuple[str, list[tuple[list[str], str]]]:
    """Return title and ordered pairs: ([chords], lyric_or_label)."""
    text = cho_path.read_text(encoding="utf-8", errors="ignore")
    title = title_from_cho(text, cho_path.stem)

    pairs: list[tuple[list[str], str]] = []
    pending_chords: list[str] = []

    for raw in text.splitlines():
        line = clean_spaces(raw).strip()
        if not line:
            continue
        if TITLE_RE.match(line):
            continue
        if line.lower().startswith("{comment: himno") or line.lower().startswith("{comment: fuente"):
            continue
        if line.upper() == "INDICE":
            continue

        # Preserve section labels without attaching chords.
        if line.lower().rstrip(":") in {"coro", "chorus"} or line == "{comment: Coro}":
            if pending_chords:
                pairs.append((pending_chords, ""))
                pending_chords = []
            pairs.append(([], "{comment: Coro}"))
            continue

        if is_plain_chord_line(line) or (INLINE_CHORD_RE.fullmatch(line) is not None):
            pending_chords.extend(line_chords(line))
            continue

        chords = pending_chords + line_chords(line)
        lyric = line_lyrics(line)
        pending_chords = []
        if lyric:
            pairs.append((chords, lyric))

    if pending_chords:
        pairs.append((pending_chords, ""))

    return title, pairs


def attach_chords(chords: list[str], lyric: str) -> str:
    lyric = lyric.strip()
    if not chords:
        return lyric
    if not lyric:
        return "".join(f"[{c}]" for c in chords)

    # Conservative placement: first chord at start; remaining chords distributed over word starts.
    words = lyric.split()
    if len(chords) == 1 or len(words) <= 1:
        return "".join(f"[{c}]" for c in chords) + lyric

    # Place chords at roughly even word positions.
    positions = []
    for idx, _ch in enumerate(chords):
        pos = round(idx * (len(words) - 1) / max(1, len(chords) - 1))
        positions.append(pos)

    by_pos: dict[int, list[str]] = {}
    for pos, chord in zip(positions, chords):
        by_pos.setdefault(pos, []).append(chord)

    out_words: list[str] = []
    for i, word in enumerate(words):
        prefix = "".join(f"[{c}]" for c in by_pos.get(i, []))
        out_words.append(prefix + word)
    return " ".join(out_words)


def find_best_clean_line(source_lyric: str, clean_lines: list[str], used: set[int], start_hint: int) -> tuple[int | None, float]:
    source_norm = normalize_for_match(source_lyric)
    if not source_norm:
        return None, 0.0

    best_i = None
    best_score = 0.0
    # Prefer nearby unused lines, but allow broader search.
    candidates = list(range(start_hint, min(len(clean_lines), start_hint + 8)))
    candidates += [i for i in range(len(clean_lines)) if i not in candidates]

    for i in candidates:
        if i in used:
            continue
        score = difflib.SequenceMatcher(None, source_norm, normalize_for_match(clean_lines[i])).ratio()
        if score > best_score:
            best_i = i
            best_score = score
        if score >= 0.98:
            break
    return best_i, best_score


def merge_one(txt_path: Path, cho_path: Path, out_path: Path) -> dict[str, int]:
    clean_lines = read_clean_lyrics(txt_path)
    title, pairs = parse_chord_source(cho_path)

    used: set[int] = set()
    next_hint = 0
    output_lines: list[str] = [f"{{title: {title}}}", ""]
    matched = 0
    unmatched = 0

    for chords, source_lyric in pairs:
        if source_lyric == "{comment: Coro}":
            output_lines.append("{comment: Coro}")
            continue

        idx, score = find_best_clean_line(source_lyric, clean_lines, used, next_hint)
        if idx is not None and score >= 0.72:
            lyric = clean_lines[idx]
            used.add(idx)
            next_hint = idx + 1
            matched += 1
        else:
            lyric = source_lyric
            unmatched += 1

        output_lines.append(attach_chords(chords, lyric))

    # Add any clean lyric lines that never matched, so the hymn remains complete.
    remaining = [clean_lines[i] for i in range(len(clean_lines)) if i not in used]
    if remaining:
        output_lines.append("")
        output_lines.append("{comment: Líneas limpias sin acordes detectados}")
        output_lines.extend(remaining)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(output_lines).strip() + "\n", encoding="utf-8")
    return {"matched": matched, "unmatched": unmatched, "remaining": len(remaining)}


def choose_matching_txt(cho_path: Path, txt_files: list[Path]) -> Path | None:
    # Prefer same stem.
    same = [p for p in txt_files if p.stem == cho_path.stem]
    if same:
        return same[0]

    cho_num = re.match(r"^(\d+)", cho_path.stem)
    if cho_num:
        n = cho_num.group(1).lstrip("0")
        for p in txt_files:
            m = re.match(r"^(\d+)", p.stem)
            if m and m.group(1).lstrip("0") == n:
                return p

    # Fuzzy title match fallback.
    cho_title = normalize_for_match(cho_path.stem)
    best = None
    best_score = 0.0
    for p in txt_files:
        score = difflib.SequenceMatcher(None, cho_title, normalize_for_match(p.stem)).ratio()
        if score > best_score:
            best = p
            best_score = score
    return best if best_score >= 0.72 else None


def main() -> None:
    if not TXT_DIR.exists():
        raise SystemExit(f"Missing clean lyrics folder: {TXT_DIR}")
    if not CHO_DIR.exists():
        raise SystemExit(f"Missing chord source folder: {CHO_DIR}")

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    txt_files = sorted(TXT_DIR.glob("*.txt"))
    cho_files = sorted(CHO_DIR.glob("*.cho"))

    created = 0
    skipped = 0
    total_matched = 0
    total_unmatched = 0
    total_remaining = 0

    for cho_path in cho_files:
        txt_path = choose_matching_txt(cho_path, txt_files)
        if not txt_path:
            skipped += 1
            continue
        out_path = OUT_DIR / cho_path.name
        stats = merge_one(txt_path, cho_path, out_path)
        created += 1
        total_matched += stats["matched"]
        total_unmatched += stats["unmatched"]
        total_remaining += stats["remaining"]

    print(f"Created: {created} files in {OUT_DIR.relative_to(ROOT)}")
    print(f"Skipped chord files without matching txt: {skipped}")
    print(f"Matched chord/lyric lines: {total_matched}")
    print(f"Unmatched chord-source lines kept: {total_unmatched}")
    print(f"Clean lyric lines added without chords: {total_remaining}")


if __name__ == "__main__":
    main()
