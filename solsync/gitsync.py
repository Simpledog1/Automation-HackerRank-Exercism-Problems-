"""Safe Git operations for the solution repositories.

Rules enforced here:
  - never force-push, reset, clean, stash, rebase or merge anything but a fast-forward
  - only ever stage/commit the exact new files we created (`git commit --only`)
  - refuse to push if the branch has commits that are not ours
  - an upload counts only after `git ls-remote` shows our commit on GitHub
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
           "LC_ALL": "C"}


class GitError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True, input: bytes | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          input=input, env=GIT_ENV, timeout=300)
    out = proc.stdout.decode("utf-8", "replace").strip()
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {' '.join(args[:2])} failed: {err[-1] if err else proc.returncode}")
    return out


def _norm_url(url: str) -> str:
    url = url.strip().rstrip("/")
    url = re.sub(r"\.git$", "", url)
    return url.lower().replace("\\", "/")


def github_owner_repo(url: str) -> str | None:
    m = re.match(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", url.strip(), re.I)
    return m.group(1) if m else None


def inspect_repo(repo: Path, remote_url: str, branch: str) -> list[str]:
    """Return a list of blocking problems (empty list = safe to work in)."""
    if not (repo / ".git").exists():
        return [f"{repo} is not a Git repository"]
    problems = []
    actual = git(repo, "remote", "get-url", "origin", check=False)
    if not actual:
        problems.append("no 'origin' remote configured")
    elif _norm_url(actual) != _norm_url(remote_url):
        problems.append(f"origin is {actual}, expected {remote_url}")
    current = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if current != branch:
        problems.append(f"current branch is '{current or 'detached'}', expected '{branch}'")
    if (repo / ".git" / "MERGE_HEAD").exists() or (repo / ".git" / "rebase-merge").exists() \
            or (repo / ".git" / "rebase-apply").exists():
        problems.append("a merge or rebase is in progress")
    return problems


def user_changes(repo: Path) -> list[str]:
    """Your uncommitted work (reported, never touched)."""
    out = git(repo, "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all")
    return [e[3:] for e in out.split("\0") if len(e) > 3]


def remote_head(repo: Path, branch: str) -> str:
    out = git(repo, "ls-remote", "origin", f"refs/heads/{branch}")
    if not out:
        raise GitError(f"branch {branch} not found on remote")
    return out.split()[0]


def tree_files(repo: Path, ref: str) -> dict[str, str]:
    """path -> blob sha for every file in a commit we have locally."""
    out = git(repo, "ls-tree", "-r", "-z", "--full-tree", ref)
    files = {}
    for entry in out.split("\0"):
        if entry:
            meta, path = entry.split("\t", 1)
            files[path] = meta.split()[2]
    return files


def github_tree_files(owner_repo: str, sha: str) -> dict[str, str]:
    """Read-only listing of a public GitHub repo at a commit (used by dry runs, no fetch)."""
    url = f"https://api.github.com/repos/{owner_repo}/git/trees/{sha}?recursive=1"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "SolutionSync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("truncated"):
        raise GitError("GitHub tree listing was truncated")
    return {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "blob"}


def remote_files(repo: Path, remote_url: str, branch: str, may_fetch: bool) -> tuple[dict[str, str], str]:
    """Files currently on GitHub. Returns (files, remote head sha)."""
    head = remote_head(repo, branch)
    if may_fetch:
        git(repo, "fetch", "--quiet", "origin", f"refs/heads/{branch}:refs/remotes/origin/{branch}")
    have_commit = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{head}^{{commit}}"],
                                 env=GIT_ENV, capture_output=True).returncode == 0
    if have_commit:
        return tree_files(repo, head), head
    owner_repo = github_owner_repo(remote_url)
    if not owner_repo:
        raise GitError("remote commit is not available locally and remote is not on GitHub")
    return github_tree_files(owner_repo, head), head


def ahead_behind(repo: Path, branch: str) -> tuple[list[str], int]:
    """(commits on local branch not on origin, number of origin commits we lack)."""
    ahead = git(repo, "rev-list", "--reverse", f"refs/remotes/origin/{branch}..refs/heads/{branch}")
    behind = git(repo, "rev-list", "--count", f"refs/heads/{branch}..refs/remotes/origin/{branch}")
    return [c for c in ahead.split() if c], int(behind or 0)


def fast_forward(repo: Path, branch: str) -> None:
    """Catch up with GitHub. Git itself refuses if this would overwrite your local edits."""
    git(repo, "merge", "--ff-only", "--quiet", f"refs/remotes/origin/{branch}")


def is_ancestor(repo: Path, commit: str, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, ref],
                          env=GIT_ENV, capture_output=True).returncode == 0


def path_is_free(repo: Path, rel: str) -> bool:
    """True only if nothing exists at rel: not on disk, not in the index, not in HEAD."""
    if os.path.lexists(repo / rel):
        return False
    if git(repo, "ls-files", "--cached", "--", rel):
        return False
    return git(repo, "ls-tree", "--name-only", "HEAD", "--", rel) == ""


def commit_new_files(repo: Path, paths: list[str], message: str) -> str:
    """Commit exactly `paths` (already written to disk). Your other staged work is untouched."""
    git(repo, "add", "--", *paths)
    git(repo, "commit", "--quiet", "--only", "-m", message, "--", *paths)
    sha = git(repo, "rev-parse", "HEAD")
    changed = git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "-z", "--root", sha)
    parts = [p for p in changed.split("\0") if p]
    pairs = sorted(zip(parts[0::2], parts[1::2]))
    if pairs != sorted(("A", p) for p in paths):
        raise GitError(f"commit {sha[:7]} contains unexpected changes: {pairs}")
    return sha


def unstage_and_remove(repo: Path, files: dict[str, str]) -> None:
    """Undo a half-finished upload of files this tool created (path -> sha256).

    A file is removed only if its content is still exactly what we wrote and it
    is not part of any commit, so your own work can never be deleted here.
    """
    import hashlib
    for p, expected in files.items():
        full = repo / p
        try:
            same = hashlib.sha256(full.read_bytes()).hexdigest() == expected
        except OSError:
            continue
        if not same or git(repo, "ls-tree", "--name-only", "HEAD", "--", p):
            continue
        git(repo, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", p, check=False)
        full.unlink()
        try:
            full.parent.rmdir()  # only succeeds if the folder is now empty
        except OSError:
            pass


def push(repo: Path, branch: str) -> None:
    git(repo, "push", "--quiet", "origin", f"refs/heads/{branch}:refs/heads/{branch}")
