"""Validated reading of user-selected text files. Reads only the file the user picked, never executes it."""
from __future__ import annotations

from pathlib import Path

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CHARS = 1_000_000
WARN_CHARS = 200_000
ALLOWED_SUFFIXES = {".txt", ".md", ".markdown", ".text"}


class InputError(ValueError):
    pass


def validate_source_text(text: str) -> str:
    """Returns text unchanged (original is never normalized) or raises InputError."""
    if not text.strip():
        raise InputError("There is no text to work with. Paste or import some text first.")
    if len(text) > MAX_CHARS:
        raise InputError(f"Text is {len(text):,} characters; the limit is {MAX_CHARS:,}.")
    if "\x00" in text:
        raise InputError("Text contains NUL characters, so it is probably not a text document.")
    return text


def read_text_file(path: str | Path) -> str:
    p = Path(path).resolve()
    if p.suffix.lower() not in ALLOWED_SUFFIXES:
        raise InputError(f"Unsupported file type {p.suffix or '(none)'}. Supported: {', '.join(sorted(ALLOWED_SUFFIXES))}.")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise InputError(f"Cannot read file: {exc.strerror or exc}") from exc
    if size > MAX_FILE_BYTES:
        raise InputError(f"File is {size / 1024 / 1024:.1f} MB; the limit is {MAX_FILE_BYTES // 1024 // 1024} MB.")
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise InputError(f"Cannot read file: {exc.strerror or exc}") from exc
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(enc) if enc != "utf-16" or raw[:2] in (b"\xff\xfe", b"\xfe\xff") else None
        except UnicodeDecodeError:
            text = None
        if text is not None:
            return validate_source_text(text)
    raise InputError("File is not valid UTF-8 or UTF-16 text.")
