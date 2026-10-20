"""HackerRank: read your official account-data export (Settings -> Export Data).

HackerRank has no official API for your submitted code. Its export (a .json file,
or a ZIP of them) includes your submissions. Put it in the inbox folder and this
module reads the newest one.

Verified layout (Oct 2026): submissions[] = {challenge, code, contest, language, score}
with no status or timestamp; score 1.0 means full marks. The reader also accepts
other key names, and refuses to guess when it cannot find code, a challenge name,
a language and proof of completion.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import zipfile
from pathlib import Path

from .models import Candidate

log = logging.getLogger(__name__)

CODE_KEYS = ("code", "source_code", "source")
SLUG_KEYS = ("challenge_slug", "slug")
NAME_KEYS = ("challenge_name", "challenge", "name", "title")  # real export: "challenge"
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

    best: dict[str, tuple[tuple[str, int], Candidate]] = {}
    for index, r in enumerate(records):
        lang = str(_first(r, LANG_KEYS) or "").strip().lower()
        evidence = _completion_evidence(r)
        if not evidence or not lang:
            continue  # only accepted / full-score submissions prove the problem was solved
        name = _first(r, NAME_KEYS)
        slug = str(_first(r, SLUG_KEYS) or "").strip().lower() or None
        title = str(name).strip() if name else slug_to_title(slug or "")
        is_python = lang.startswith(("python", "pypy"))
        # python3 and pypy3 solutions of one challenge go to the same file, so share one id
        item_id = f"{slug or re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')}:" \
                  f"{'python' if is_python else lang}"
        when = str(_first(r, TIME_KEYS) or "")
        c = Candidate(
            platform="hackerrank", item_id=item_id, title=title, language=lang,
            # Without a slug the real problem URL is unknown; don't invent one.
            url=f"https://www.hackerrank.com/challenges/{slug}/problem" if slug else "",
            method=f"official account export ({path.name})",
            evidence=evidence + (f" at {when}" if when else ""),
            completed_at=when,
            files={"1.py" if is_python else "solution.txt": str(_first(r, CODE_KEYS)).encode("utf-8")},
            source_ref=str(r.get("id", "")),
            title_from_slug=not name,
        )
        # Keep the newest qualifying submission. The export has no timestamps, so a
        # later position counts as newer (shortcut: the file appears to list oldest
        # first; revisit if HackerRank adds timestamps or reorders the export).
        order = (when, index)
        if c.key not in best or order > best[c.key][0]:
            best[c.key] = (order, c)
    out = sorted((c for _, c in best.values()), key=lambda c: (c.completed_at, c.item_id))
    return out, len(records)


SECTIONS_FILE = Path(__file__).with_name("hackerrank_sections.json")


def assign_sections(cands: list[Candidate], language_dirs: dict[str, str]) -> None:
    """Set each candidate's repo folder from its HackerRank section.

    Only languages listed in config (the Python family) get a folder; the section
    list comes from scripts/build_hackerrank_sections.py. Unknown sections and other
    languages keep top_dir "" and go to review.
    """
    from .matching import normalize
    sections = json.loads(SECTIONS_FILE.read_text(encoding="utf-8"))
    folder_by_name = {normalize(n): folder for folder, names in sections.items() for n in names}
    for c in cands:
        if c.language in language_dirs:
            c.top_dir = folder_by_name.get(normalize(c.title), "")


def _completion_evidence(r: dict) -> str | None:
    """Why this submission counts as solved, or None if it doesn't."""
    status = str(_first(r, STATUS_KEYS) or "").strip().lower()
    if status:
        return "submission status 'Accepted'" if status in ACCEPTED else None
    score = r.get("score")
    # The real export has no status; score 1.0 means all test cases passed.
    if isinstance(score, (int, float)) and score >= 0.999999:
        return "full score (1.0) in official export"
    return None
