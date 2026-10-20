"""Decide whether a candidate is already on GitHub, and where a new one should go."""
from __future__ import annotations

import ast
import hashlib
import re
import unicodedata

from .models import (AMBIGUOUS, CONFLICT, ELIGIBLE, NEEDS_REVIEW, NEEDS_SOURCE,
                     PRESENT, Candidate)

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
MAX_FILE_BYTES = 200_000
SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"(?i)\b(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
]


def normalize(title: str) -> str:
    """'Guido's Gorgeous Lasagna' and 'guidos-gorgeous-lasagna' -> 'guidosgorgeouslasagna'."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())


def git_blob_sha(content: bytes) -> str:
    """The same id Git gives a file, so we can compare content without downloading it."""
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_name(name: str, max_len: int = 100) -> str | None:
    """Make a platform title safe as one Windows path part, or None if impossible."""
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name or name in (".", "..") or len(name) > max_len:
        return None
    if name.split(".")[0].upper() in WINDOWS_RESERVED:
        return None
    return name


def safe_file_name(name: str) -> str | None:
    """Solution file names must be plain names like 'bob.py' (no folders)."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}", name or ""):
        return None
    return safe_name(name)


def validate_source(files: dict[str, bytes]) -> str | None:
    """Static checks only (code is never executed). Returns a problem, or None if OK."""
    if not files:
        return "no solution files"
    for name, data in files.items():
        if not data.strip():
            return f"{name} is empty"
        if len(data) > MAX_FILE_BYTES:
            return f"{name} is suspiciously large ({len(data)} bytes)"
        if b"\x00" in data:
            return f"{name} looks like binary data"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return f"{name} is not UTF-8 text"
        if any(p.search(data) for p in SECRET_PATTERNS):
            return f"{name} may contain a secret"
        if name.endswith(".py"):
            try:
                ast.parse(text)  # parses only; does not run the code
            except SyntaxError as exc:
                return f"{name} has a Python syntax error (line {exc.lineno}); possibly truncated"
    return None


def exercise_dirs(repo_files: dict[str, str], top_dir: str | None) -> dict[str, list[str]]:
    """normalized exercise folder name -> [folder paths] under one top folder (None = any)."""
    out: dict[str, list[str]] = {}
    for path in repo_files:
        parts = path.split("/")
        if len(parts) >= 3 and top_dir in (None, parts[0]):
            folder = f"{parts[0]}/{parts[1]}"
            bucket = out.setdefault(normalize(parts[1]), [])
            if folder not in bucket:
                bucket.append(folder)
    return out


def classify(c: Candidate, repo_files: dict[str, str], language_dirs: dict[str, str]) -> None:
    """Set c.status, c.dest_paths and c.note.

    repo_files maps every path on GitHub to its git blob sha.
    """
    # 1. Same content anywhere in the repo => already uploaded (maybe under another name).
    if c.files:
        blob_to_path = {sha: p for p, sha in repo_files.items()}
        # Git may store a CRLF file with LF endings, so try both forms.
        shas = {git_blob_sha(v) for d in c.files.values() for v in (d, d.replace(b"\r\n", b"\n"))}
        hits = sorted(blob_to_path[s] for s in shas if s in blob_to_path)
        if hits:
            c.status, c.note = PRESENT, f"same content at {hits[0]}"
            return

    if c.platform == "hackerrank":
        # Folder comes from the challenge's section. If unknown, still look for an
        # existing folder of that name anywhere, so nothing gets uploaded twice.
        top = c.top_dir or None
    else:
        top = language_dirs.get(c.language)
        if not top:
            c.status, c.note = NEEDS_REVIEW, f"no folder convention for language '{c.language}'"
            return

    # 2. A folder with the same normalized exercise name => already uploaded.
    dirs = exercise_dirs(repo_files, top)
    names = {normalize(c.title), normalize(c.item_id.split("/")[-1].split(":")[0])}
    matches = sorted({d for n in names if n for d in dirs.get(n, [])})
    if len(matches) > 1:
        c.status, c.note = AMBIGUOUS, "several folders match: " + ", ".join(matches)
        return
    if len(matches) == 1:
        c.status, c.dest_paths = PRESENT, [matches[0]]
        c.note = f"folder exists: {matches[0]} (not overwritten)"
        return

    # 3. Genuinely missing. We need the source to know file names and validate it.
    if not top:
        c.status, c.note = NEEDS_REVIEW, ("no folder rule: not a Python or Problem Solving "
                                          f"challenge in {c.language} (place it by hand)")
        return
    if c.files is None:
        c.status = NEEDS_SOURCE
        return
    if c.title_from_slug:
        # e.g. slug "validate-a-roman-number" vs folder "Validating Roman Numerals":
        # without the real title we cannot rule out a duplicate under another name.
        c.status, c.note = NEEDS_REVIEW, "only the challenge slug is known, not its title"
        return
    folder = safe_name(c.title)
    if not folder:
        c.status, c.note = NEEDS_REVIEW, f"title '{c.title}' cannot be a safe folder name"
        return
    dests = []
    for name in sorted(c.files):
        fname = safe_file_name(name)
        if not fname:
            c.status, c.note = NEEDS_REVIEW, f"unsafe file name '{name}'"
            return
        dests.append(f"{top}/{folder}/{fname}")
    taken = [d for d in dests if d in repo_files or any(p.lower() == d.lower() for p in repo_files)]
    if taken:
        c.status, c.note = CONFLICT, "path already used: " + ", ".join(taken)
        return
    problem = validate_source(c.files)
    if problem:
        c.status, c.note = NEEDS_REVIEW, problem
        return
    c.status, c.dest_paths = ELIGIBLE, dests
