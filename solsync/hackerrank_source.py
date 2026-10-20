"""HackerRank: read your official account-data export (Settings -> Export Data).

HackerRank has no official API for your submitted code. Its documented export is a
ZIP of JSON files that includes your submissions. Download it into the inbox folder
and this module reads the newest one.

The export's exact JSON layout is not publicly documented, so the reader looks for
submission-like records by common key names and refuses to guess when it cannot
find code, a challenge name, a language and an "Accepted" status.
"""
from __future__ import annotations

import gzip
import json
import logging
import zipfile
from pathlib import Path

from .models import Candidate

log = logging.getLogger(__name__)

CODE_KEYS = ("code", "source_code", "source")
SLUG_KEYS = ("challenge_slug", "slug")
NAME_KEYS = ("challenge_name", "name", "title")
LANG_KEYS = ("language", "lang")
STATUS_KEYS = ("status", "verdict", "result")
TIME_KEYS = ("created_at", "submitted_at", "time")
ACCEPTED = {"accepted"}
MAX_EXPORT_BYTES = 200_000_000


class ExportFormatError(Exception):
    pass


def newest_export(inbox: Path) -> Path | None:
    files = [p for p in inbox.glob("*") if p.is_file()
             and p.suffix.lower() in (".zip", ".json", ".gz")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _load_json_docs(path: Path) -> list:
    docs = []

    def parse(name: str, data: bytes) -> None:
        if name.lower().endswith(".gz"):
            data = gzip.decompress(data)
            name = name[:-3]
        if name.lower().endswith(".json"):
            try:
                docs.append(json.loads(data))
            except ValueError:
                log.warning("Skipping unreadable JSON in export: %s", name)

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            total = sum(i.file_size for i in zf.infolist())
            if total > MAX_EXPORT_BYTES:
                raise ExportFormatError("export is unexpectedly large; refusing to unpack")
            for info in zf.infolist():
                if not info.is_dir():
                    parse(info.filename, zf.read(info))
    else:
        parse(path.name, path.read_bytes())
    return docs


def _first(d: dict, keys: tuple[str, ...]):
    for k in keys:
        v = d.get(k)
        if isinstance(v, dict):  # e.g. "challenge": {"slug": ..., "name": ...}
            continue
        if v not in (None, ""):
            return v
    return None


def _walk(node, found: list) -> None:
    if isinstance(node, dict):
        nested = node.get("challenge") if isinstance(node.get("challenge"), dict) else {}
        merged = {**nested, **node} if nested else node
        # Requiring a language stops look-alikes such as {"code": "PH", "name": "Philippines"}.
        if _first(merged, CODE_KEYS) and _first(merged, LANG_KEYS) and \
                (_first(merged, SLUG_KEYS) or _first(merged, NAME_KEYS)):
            found.append(merged)
            return
        for v in node.values():
            _walk(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, found)


def slug_to_title(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w)


def read_export(path: Path) -> tuple[list[Candidate], int]:
    """Return (accepted candidates, number of submission records seen)."""
    records: list[dict] = []
    for doc in _load_json_docs(path):
        _walk(doc, records)
    if not records:
        raise ExportFormatError(
            "no submission records with source code found; the export format may differ "
            "from what this reader expects (see TROUBLESHOOTING.md)")

    best: dict[str, Candidate] = {}
    for r in records:
        status = str(_first(r, STATUS_KEYS) or "").strip().lower()
        lang = str(_first(r, LANG_KEYS) or "").strip().lower()
        if status not in ACCEPTED or not lang:
            continue  # only accepted submissions prove the problem was solved
        name = _first(r, NAME_KEYS)
        slug = str(_first(r, SLUG_KEYS) or "").strip().lower() or None
        title = str(name).strip() if name else slug_to_title(slug or "")
        slug = slug or title.lower().replace(" ", "-")
        code = _first(r, CODE_KEYS)
        when = str(_first(r, TIME_KEYS) or "")
        c = Candidate(
            platform="hackerrank", item_id=f"{slug}:{lang}", title=title, language=lang,
            url=f"https://www.hackerrank.com/challenges/{slug}/problem",
            method=f"official account export ({path.name})",
            evidence=f"submission status 'Accepted'" + (f" at {when}" if when else ""),
            completed_at=when, files={"1.py" if lang.startswith(("python", "pypy")) else "solution.txt":
                                      str(code).encode("utf-8")},
            source_ref=str(r.get("id", "")),
            title_from_slug=not name,
        )
        # keep the newest accepted submission per challenge + language
        if c.key not in best or when > best[c.key].completed_at:
            best[c.key] = c
    out = sorted(best.values(), key=lambda c: (c.completed_at, c.item_id))
    return out, len(records)
