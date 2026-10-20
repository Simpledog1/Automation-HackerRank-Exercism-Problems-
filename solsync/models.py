"""The Candidate record and the status names used everywhere."""
from __future__ import annotations

from dataclasses import dataclass, field

# Status names (shown in logs, dry-run output and the state database)
PRESENT = "already_present"          # already in the GitHub repo
ELIGIBLE = "eligible"                # missing from GitHub and safe to upload
NEEDS_SOURCE = "needs_source"        # not matched yet; source not downloaded yet
MISSING_SOURCE = "missing_source"    # completed, but source code could not be found
AMBIGUOUS = "ambiguous_match"        # more than one possible match; needs your review
CONFLICT = "conflict"                # destination path taken by different content
NEEDS_REVIEW = "needs_review"        # e.g. failed validation, no folder convention
RETRIEVAL_FAILED = "retrieval_failed"
DEFERRED = "deferred_quota"          # eligible, but today's quota is used up
UPLOADED = "uploaded_verified"
UPLOAD_FAILED = "upload_failed"


@dataclass
class Candidate:
    platform: str                 # "hackerrank" or "exercism"
    item_id: str                  # stable id, e.g. "python/bob" or "triangle-quest:python3"
    title: str                    # display title, e.g. "Triangle Quest"
    language: str                 # HackerRank language or Exercism track slug
    url: str
    method: str                   # how the source was retrieved
    evidence: str                 # why we believe it is completed
    completed_at: str = ""
    files: dict[str, bytes] | None = None   # file name -> content; None = not downloaded yet
    source_ref: str = ""          # submission id / solution uuid
    title_from_slug: bool = False  # True if the real display title was not available
    status: str = ""
    dest_paths: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.item_id}"
