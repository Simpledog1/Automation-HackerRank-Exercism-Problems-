"""Exercism: list completed solutions and download your submitted files.

Uses the same API as the official Exercism CLI (api.exercism.org):
  - GET /v2/solutions?page=N        -> your solutions (status, exercise, track, uuid)
  - GET /v1/solutions/{uuid}        -> file list + download base URL (what `exercism download` uses)
  - each solution includes .exercism/config.json, whose files.solution lists YOUR files
The token comes from Windows Credential Manager (never from config or logs).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .credentials import read_generic_credential
from .models import Candidate

log = logging.getLogger(__name__)
USER_AGENT = "SolutionSync/1.0 (personal solution backup)"
COMPLETED = {"completed", "published"}
TRUSTED_HOSTS = ("exercism.org", "exercism.io")  # and their subdomains


class AuthError(Exception):
    pass


class TransientError(Exception):
    pass


class ExercismClient:
    def __init__(self, api_base: str, token: str, delay: float = 1.0):
        self.api_base = api_base
        self._token = token
        self.delay = delay  # be polite: pause between requests

    def _get(self, url: str) -> bytes:
        # The token must only ever go to Exercism, even if an API response names another host.
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        trusted = any(host == h or host.endswith("." + h) for h in TRUSTED_HOSTS)
        if urllib.parse.urlsplit(url).scheme != "https" or not trusted:
            raise RuntimeError(f"refusing to send the Exercism token to {host or url}")
        time.sleep(self.delay)
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._token}", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and b"<html" in exc.read(2000).lower():
                # A website bot-check page, not a token problem.
                raise RuntimeError("Exercism's website blocked an automated request (HTTP 403)") from None
            if exc.code in (401, 403):
                raise AuthError(f"Exercism rejected the token (HTTP {exc.code})") from None
            if exc.code == 429 or exc.code >= 500:
                raise TransientError(f"Exercism HTTP {exc.code}") from None
            raise RuntimeError(f"Exercism HTTP {exc.code} for {urllib.parse.urlsplit(url).path}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TransientError(f"network error: {exc}") from None

    def _json(self, path: str) -> dict:
        return json.loads(self._get(self.api_base + path))

    def list_solutions(self, max_pages: int = 50) -> list[dict]:
        out, page = [], 1
        while page <= max_pages:
            data = self._json(f"/v2/solutions?page={page}")
            results = data.get("results")
            if not isinstance(results, list):
                raise RuntimeError("unexpected /v2/solutions response (no 'results' list)")
            out.extend(results)
            # Trust total_pages only if present; otherwise read until an empty page,
            # so a different response shape can never silently hide page 2+.
            total = (data.get("meta") or {}).get("total_pages")
            if not results or (total and page >= total):
                break
            page += 1
        return out

    def download_solution_files(self, uuid: str) -> dict[str, bytes]:
        """Return only the files Exercism marks as the solution (not tests/README)."""
        meta = self._json(f"/v1/solutions/{urllib.parse.quote(uuid)}")["solution"]
        if not (meta.get("user") or {}).get("is_requester", False):
            raise RuntimeError("solution does not belong to the authenticated user")
        # The API returns links on exercism.org/api/..., which shows scripts a bot-check
        # page; the same files are served by the official API host the CLI uses.
        base = meta["file_download_base_url"].replace("https://exercism.org/api/", self.api_base + "/", 1)
        files = meta.get("files") or []
        if ".exercism/config.json" not in files:
            raise RuntimeError("no .exercism/config.json; cannot tell which files are yours")
        config = json.loads(self._get(base + ".exercism/config.json"))
        wanted = (config.get("files") or {}).get("solution") or []
        result = {}
        for name in wanted:
            parts = name.split("/")
            # Some tracks keep the solution in a subfolder (Dart: lib/hello_world.dart).
            # Save it flat under its file name, like every other exercise in the repo.
            if name not in files or "\\" in name or any(p in ("", ".", "..") or p.startswith(".")
                                                         for p in parts):
                raise RuntimeError(f"solution file '{name}' is missing or not a safe path")
            if parts[-1] in result:
                raise RuntimeError(f"two solution files are both named '{parts[-1]}'")
            result[parts[-1]] = self._get(base + urllib.parse.quote(name))
        return result


def make_client(api_base: str, credential_target: str) -> ExercismClient:
    token = read_generic_credential(credential_target)
    if not token:
        raise AuthError(f"No Exercism token in Windows Credential Manager entry '{credential_target}'")
    return ExercismClient(api_base, clean_token(token))


def clean_token(stored: str) -> str:
    """Exercism tokens are UUIDs. A paste into a hidden prompt can repeat it or add
    stray text, so use the UUID when exactly one distinct one is present."""
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                         stored.lower()))
    return ids.pop() if len(ids) == 1 else stored.strip()


def to_candidates(solutions: list[dict]) -> list[Candidate]:
    """Turn API results into candidates. Only completed/published count as done.

    Tracks without a folder convention in config.json end up as 'needs_review'.
    """
    out = []
    for s in solutions:
        track = (s.get("track") or {}).get("slug", "")
        ex = s.get("exercise") or {}
        if not track or not ex.get("slug") or not s.get("uuid"):
            continue
        status = s.get("status", "")
        if status not in COMPLETED:
            continue  # started/iterated but not marked complete: not eligible
        out.append(Candidate(
            platform="exercism",
            item_id=f"{track}/{ex['slug']}",
            title=ex.get("title") or ex["slug"],
            language=track,
            url=s.get("private_url") or s.get("public_url") or "",
            method="exercism api v2 list + v1 download (CLI API)",
            evidence=f"Exercism status '{status}'" + (
                f", completed_at {s['completed_at']}" if s.get("completed_at") else ""),
            completed_at=s.get("completed_at") or "",
            source_ref=s["uuid"],
        ))
    out.sort(key=lambda c: (c.completed_at, c.item_id))
    return out
