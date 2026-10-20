"""One sync run, start to finish. See README.md for the step-by-step workflow."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field

from . import exercism_source, gitsync, hackerrank_source
from .config import Config, RepoConfig, manila_day, manila_now
from .ledger import Ledger, is_due, retry_after
from .lock import AlreadyRunning, RunLock
from .matching import classify, sha256
from .models import (AMBIGUOUS, CONFLICT, DEFERRED, ELIGIBLE, MISSING_SOURCE,
                     NEEDS_REVIEW, NEEDS_SOURCE, PRESENT, RETRIEVAL_FAILED,
                     UPLOAD_FAILED, UPLOADED, Candidate)

log = logging.getLogger("solsync")
PLATFORMS = ("hackerrank", "exercism")
MAX_ATTEMPTS = 5
EXIT_OK, EXIT_PARTIAL, EXIT_FAILED, EXIT_LOCKED = 0, 1, 2, 3


def other(platform: str) -> str:
    return "exercism" if platform == "hackerrank" else "hackerrank"


@dataclass
class PlatformState:
    access: str = "not checked"
    repo_ok: bool = False
    problems: list[str] = field(default_factory=list)
    user_changes: list[str] = field(default_factory=list)
    records_seen: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    remote_files: dict[str, str] = field(default_factory=dict)
    failures: int = 0


# ---------------------------------------------------------------- selection
def select(eligible: dict[str, list[Candidate]], remaining: int, first: str
           ) -> tuple[list[Candidate], list[Candidate]]:
    """Alternate between platforms, starting with `first`, until the quota is used.

    An exercise is only taken if ALL its files fit in the remaining quota.
    A platform with nothing (that fits) simply passes its turn to the other.
    """
    order = [first, other(first)]
    queues = {p: list(eligible.get(p, [])) for p in order}
    selected: list[Candidate] = []
    turn = 0
    while remaining > 0 and any(queues.values()):
        q = queues[order[turn % 2]]
        turn += 1
        for i, c in enumerate(q):
            if len(c.dest_paths) <= remaining:
                selected.append(q.pop(i))
                remaining -= len(c.dest_paths)
                break
        else:
            q.clear()  # nothing from this platform fits today
    deferred = [c for p in order for c in eligible.get(p, []) if c not in selected]
    return selected, deferred


# ---------------------------------------------------------------- git steps
def _verify_and_record(repo: RepoConfig, ledger: Ledger, batches: list[dict]) -> int:
    """Mark batches verified if GitHub has their commit. Returns number verified."""
    head = gitsync.remote_head(repo.path, repo.branch)
    gitsync.git(repo.path, "fetch", "--quiet", "origin",
                f"refs/heads/{repo.branch}:refs/remotes/origin/{repo.branch}")
    done = 0
    for b in batches:
        if gitsync.is_ancestor(repo.path, b["commit_sha"], head):
            ledger.set_batch(b["batch_id"], "verified")
            done += 1
            log.info("Verified on GitHub: %s (%s)", b["item_key"], b["commit_sha"][:7])
    return done


def push_pending(cfg: Config, repo: RepoConfig, ledger: Ledger, today: str) -> list[str]:
    """Push our committed-but-unpushed batches. Returns problems (empty = fine)."""
    pending = [b for b in ledger.open_batches(repo.platform) if b["status"] == "committed"]
    ahead, behind = gitsync.ahead_behind(repo.path, repo.branch)
    ours = {b["commit_sha"] for b in pending}
    if not ahead:
        return []
    if not set(ahead) <= ours:
        return ["local branch has commits that were not made by this tool; not pushing "
                "(push or remove them yourself)"]
    if behind:
        return ["local and GitHub histories have diverged; not pushing (see TROUBLESHOOTING.md)"]
    to_push = [b for b in pending if b["commit_sha"] in set(ahead)]
    # A retry on a later day is charged to today's quota; wait if today is full.
    for b in to_push:
        if not ledger.move_batch_to_day(b["batch_id"], today, cfg.daily_limit):
            return [f"{b['item_key']} is committed locally but today's quota is full; "
                    "it will be pushed on a later day"]
    try:
        gitsync.push(repo.path, repo.branch)
    except gitsync.GitError as exc:
        log.warning("Push failed: %s (will check GitHub before retrying)", exc)
    if _verify_and_record(repo, ledger, to_push) < len(to_push):
        for b in to_push:
            ledger.set_batch(b["batch_id"], "committed", error="push not confirmed")
        return ["push did not reach GitHub; the commit is kept and will be retried"]
    return []


def reconcile(cfg: Config, repo: RepoConfig, ledger: Ledger, today: str) -> list[str]:
    """Finish or roll back uploads that an earlier run left unfinished."""
    batches = ledger.open_batches(repo.platform)
    if not batches:
        return []
    log.info("%s: reconciling %d unfinished upload(s)", repo.platform, len(batches))
    for b in batches:
        paths = [p for p, _ in b["files"]]
        if not b["commit_sha"]:
            # Crashed after reserving quota but before recording a commit.
            in_head = gitsync.git(repo.path, "ls-tree", "--name-only", "HEAD", "--", paths[0])
            if in_head:
                sha = gitsync.git(repo.path, "log", "-1", "--format=%H",
                                  f"refs/heads/{repo.branch}", "--", paths[0])
                ledger.set_batch(b["batch_id"], "committed", commit_sha=sha)
                b["commit_sha"], b["status"] = sha, "committed"
            else:
                gitsync.unstage_and_remove(repo.path, dict(b["files"]))
                ledger.set_batch(b["batch_id"], "failed", error="interrupted before commit")
                continue
        try:
            gitsync.git(repo.path, "fetch", "--quiet", "origin",
                        f"refs/heads/{repo.branch}:refs/remotes/origin/{repo.branch}")
        except gitsync.GitError as exc:
            return [f"cannot fetch from GitHub: {exc}"]
        if gitsync.is_ancestor(repo.path, b["commit_sha"], f"refs/remotes/origin/{repo.branch}"):
            ledger.set_batch(b["batch_id"], "verified")  # pushed earlier; state was not saved
        elif not gitsync.is_ancestor(repo.path, b["commit_sha"], f"refs/heads/{repo.branch}"):
            ledger.set_batch(b["batch_id"], "failed", error="commit no longer on the branch")
    return push_pending(cfg, repo, ledger, today)


def upload(cfg: Config, repo: RepoConfig, ledger: Ledger, c: Candidate, today: str) -> None:
    """Write, commit, push and verify one exercise. Sets c.status."""
    root = repo.path.resolve()
    pairs = list(zip(sorted(c.files), c.dest_paths))
    for _, rel in pairs:
        if root not in (root / rel).resolve().parents or not gitsync.path_is_free(repo.path, rel):
            c.status, c.note = CONFLICT, f"{rel} already exists locally or is outside the repo"
            return
    ahead, behind = gitsync.ahead_behind(repo.path, repo.branch)
    if ahead or behind:
        c.status, c.note = UPLOAD_FAILED, "branch not in sync with GitHub"
        return
    hashes = {rel: sha256(c.files[name]) for name, rel in pairs}
    batch = ledger.reserve(today, cfg.daily_limit, c.platform, c.key, list(hashes.items()))
    if not batch:
        c.status = DEFERRED
        return
    created: dict[str, str] = {}
    try:
        for name, rel in pairs:
            full = repo.path / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            with open(full, "xb") as fh:  # "x" = fail rather than overwrite
                fh.write(c.files[name])
            created[rel] = hashes[rel]
        message = f"Add {c.title} solution\n\n" + (f"Source: {c.url}\n" if c.url else "") \
            + "Uploaded by SolutionSync"
        sha = gitsync.commit_new_files(repo.path, [rel for _, rel in pairs], message)
    except Exception as exc:
        gitsync.unstage_and_remove(repo.path, created)
        ledger.set_batch(batch, "failed", error=str(exc))
        c.status, c.note = UPLOAD_FAILED, f"commit failed: {exc}"
        log.error("%s: %s", c.key, c.note)
        return
    ledger.set_batch(batch, "committed", commit_sha=sha)
    log.info("Committed %s as %s", c.key, sha[:7])
    problems = push_pending(cfg, repo, ledger, today)
    rows = ledger.db.execute("SELECT status FROM uploads WHERE batch_id=?", (batch,)).fetchall()
    if rows and rows[0][0] == "verified":
        c.status, c.note = UPLOADED, f"commit {sha[:7]} verified on GitHub"
    else:
        c.status, c.note = UPLOAD_FAILED, "; ".join(problems) or "push not verified"


# ---------------------------------------------------------------- discovery
def discover(cfg: Config, st: dict[str, PlatformState]) -> exercism_source.ExercismClient | None:
    hr = st["hackerrank"]
    export = hackerrank_source.newest_export(cfg.hackerrank_inbox) \
        if cfg.hackerrank_inbox.exists() else None
    if not export:
        hr.access = f"no export file in {cfg.hackerrank_inbox} (manual download needed)"
    else:
        try:
            hr.candidates, hr.records_seen = hackerrank_source.read_export(export)
            hr.access = f"ok (export {export.name})"
        except (hackerrank_source.ExportFormatError, OSError, ValueError) as exc:
            hr.access, hr.failures = f"export unreadable: {exc}", hr.failures + 1

    ex = st["exercism"]
    try:
        client = exercism_source.make_client(cfg.exercism_api_base, cfg.exercism_credential_target)
        solutions = client.list_solutions()
        ex.records_seen = len(solutions)
        ex.candidates = exercism_source.to_candidates(solutions)
        ex.access = "ok (token accepted)"
        return client
    except exercism_source.AuthError as exc:
        ex.access = f"auth problem: {exc}"
    except Exception as exc:
        ex.access, ex.failures = f"unavailable: {exc}", ex.failures + 1
    return None


def fetch_sources(cfg: Config, ledger: Ledger, client, cands: list[Candidate],
                  state: PlatformState, save: bool) -> None:
    budget = cfg.max_downloads_per_run
    # Never-seen items first, so old stuck items (conflicts, failed checks) can't
    # use up the whole download budget every run and starve newer solutions.
    for c in sorted(cands, key=lambda c: ledger.item(c.key) is not None):
        if c.status != NEEDS_SOURCE:
            continue
        prev = ledger.item(c.key)
        if prev and prev["attempts"] >= MAX_ATTEMPTS:
            c.status, c.note = RETRIEVAL_FAILED, "gave up after repeated failures; run reset-item"
            continue
        if prev and not is_due(prev["next_retry"]):
            c.status, c.note = RETRIEVAL_FAILED, f"waiting to retry after {prev['next_retry']}"
            continue
        if client is None or budget <= 0:
            c.note = "source not downloaded this run (download limit)"
            continue
        budget -= 1
        try:
            c.files = client.download_solution_files(c.source_ref)
        except exercism_source.AuthError as exc:
            c.status, c.note = RETRIEVAL_FAILED, str(exc)
            client = None  # stop; do not hammer the API with a bad token
        except Exception as exc:
            transient = isinstance(exc, exercism_source.TransientError)
            c.status = RETRIEVAL_FAILED if transient else MISSING_SOURCE
            c.note = str(exc)
            state.failures += 1
            if save:
                attempts = (prev["attempts"] if prev else 0) + 1
                ledger.save_item(c, attempts_delta=1, next_retry=retry_after(attempts))
                c.note += " (saved for retry)"


# ---------------------------------------------------------------- main run
def run(cfg: Config, dry_run: bool) -> tuple[int, dict]:
    run_id = uuid.uuid4().hex[:8]
    started = manila_now()
    today = manila_day(started)
    live = cfg.uploads_enabled and not dry_run
    mode = "live" if live else ("dry-run" if dry_run else "dry-run (uploads_enabled is false)")
    summary: dict = {"run_id": run_id, "mode": mode, "started": started.isoformat(timespec="seconds"),
                     "manila_day": today}
    try:
        with RunLock(cfg.state_dir / "run.lock"):
            code = _run_locked(cfg, live, today, summary)
    except AlreadyRunning as exc:
        summary["status"], code = f"skipped: {exc}", EXIT_LOCKED
    except Exception as exc:
        log.exception("Run failed")
        summary["status"], code = f"failed: {exc}", EXIT_FAILED
    summary["finished"] = manila_now().isoformat(timespec="seconds")
    summary["exit_code"] = code
    for p, info in summary.get("platforms", {}).items():
        log.info("%s: access=%s repo_ok=%s problems=%s", p, info["access"], info["repo_ok"],
                 info["repo_problems"])
        for it in info["items"]:
            log.info("  %s %s %s %s", it["status"], it["item"], it["dest"], it["note"])
    log.info("Run %s finished: status=%s exit=%s counts=%s quota=%s", run_id,
             summary.get("status"), code, summary.get("counts"), summary.get("quota"))
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        name = "last_run.json" if live else "last_dry_run.json"
        (cfg.state_dir / name).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except OSError:
        log.exception("Could not write run summary")
    return code, summary


def _run_locked(cfg: Config, live: bool, today: str, summary: dict) -> int:
    ledger = Ledger(cfg.state_dir / "solsync.db")
    try:
        st = {p: PlatformState() for p in PLATFORMS}
        log.info("Run %s started (%s), Manila day %s", summary["run_id"], summary["mode"], today)

        # 1. Repositories: check, finish earlier uploads, read what is on GitHub.
        for p in PLATFORMS:
            repo, s = cfg.repos[p], st[p]
            s.problems = gitsync.inspect_repo(repo.path, repo.remote_url, repo.branch)
            if s.problems:
                continue
            s.user_changes = gitsync.user_changes(repo.path)
            try:
                if live:
                    s.problems += reconcile(cfg, repo, ledger, today)
                s.remote_files, _ = gitsync.remote_files(repo.path, repo.remote_url, repo.branch,
                                                         may_fetch=live)
                if live and not s.problems:
                    ahead, behind = gitsync.ahead_behind(repo.path, repo.branch)
                    if ahead and behind:
                        s.problems.append("local and GitHub histories have diverged")
                    elif ahead:
                        s.problems.append("local branch has unpushed commits not made by this tool")
                    elif behind:
                        gitsync.fast_forward(repo.path, repo.branch)
                        log.info("%s: fast-forwarded %d commit(s) from GitHub", p, behind)
            except Exception as exc:  # network down, auth, ff refused because of your edits...
                s.problems.append(f"repository check failed: {exc}")
            s.repo_ok = not s.problems

        # 2. Discover completed solutions on each platform.
        client = discover(cfg, st)

        # 3. Compare with GitHub; download source only for unmatched ones.
        open_keys = {b["item_key"] for p in PLATFORMS for b in ledger.open_batches(p)}
        for p in PLATFORMS:
            s, repo = st[p], cfg.repos[p]
            s.candidates = [c for c in s.candidates if c.key not in open_keys]
            for c in s.candidates:
                classify(c, s.remote_files, repo.language_dirs)
            if p == "exercism":
                fetch_sources(cfg, ledger, client, s.candidates, s, save=live)
                for c in s.candidates:
                    if c.files is not None and c.status == NEEDS_SOURCE:
                        classify(c, s.remote_files, repo.language_dirs)

        # 4. Pick at most the remaining quota, alternating platforms fairly.
        used = ledger.used_on(today)
        remaining = max(0, cfg.daily_limit - used)
        first = ledger.get_meta("next_platform", "hackerrank")
        eligible = {p: [c for c in st[p].candidates if c.status == ELIGIBLE]
                    for p in PLATFORMS if st[p].repo_ok}
        selected, deferred = select(eligible, remaining, first)
        for c in deferred:
            c.status, c.note = DEFERRED, "daily quota reached; queued for a later day"

        # 5. Upload (live) or describe (dry run).
        plan = []
        for c in selected:
            repo = cfg.repos[c.platform]
            if live:
                upload(cfg, repo, ledger, c, today)
                if c.status == UPLOADED:
                    ledger.set_meta("next_platform", other(c.platform))
                    ledger.set_meta("last_success", manila_now().isoformat(timespec="seconds"))
            plan.append({"item": c.key, "platform": c.platform, "files": c.dest_paths,
                         "result": c.status if live else "would upload",
                         "git": ("git add -- <files>; git commit --only -- <files>; "
                                 f"git push origin {repo.branch}; verify with git ls-remote")})
        if live:
            for p in PLATFORMS:
                for c in st[p].candidates:
                    ledger.save_item(c)

        return _summarize(cfg, st, ledger, today, first, plan, summary, live)
    finally:
        ledger.close()


def _summarize(cfg, st, ledger, today, first, plan, summary, live) -> int:
    def count(status: str) -> int:
        return sum(1 for p in PLATFORMS for c in st[p].candidates if c.status == status)

    platforms = {}
    for p in PLATFORMS:
        s = st[p]
        platforms[p] = {
            "access": s.access, "repo_ok": s.repo_ok, "repo_problems": s.problems,
            "your_uncommitted_changes": s.user_changes,
            "records_seen": s.records_seen, "completed_found": len(s.candidates),
            "items": [{"item": c.key, "title": c.title, "status": c.status, "note": c.note,
                       "dest": c.dest_paths, "evidence": c.evidence}
                      for c in s.candidates if c.status != PRESENT],
        }
    uploaded = count(UPLOADED)
    failures = (count(RETRIEVAL_FAILED) + count(MISSING_SOURCE) + count(UPLOAD_FAILED)
                + sum(s.failures for s in st.values()))
    blocked = any(not s.repo_ok or not s.access.startswith("ok") for s in st.values())
    summary.update({
        "platforms": platforms,
        "counts": {
            "completed_found": sum(len(s.candidates) for s in st.values()),
            "source_retrieved": sum(1 for s in st.values() for c in s.candidates if c.files),
            "already_present": count(PRESENT),
            "new_candidates": count(DEFERRED) + len(plan),
            "selected": len(plan),
            "uploaded_verified": uploaded,
            "deferred": count(DEFERRED),
            "conflicts_or_ambiguous": count(CONFLICT) + count(AMBIGUOUS),
            "needs_review": count(NEEDS_REVIEW),
            "failures": failures,
        },
        "fairness": f"started with {first}, alternating; next run starts with "
                    f"{ledger.get_meta('next_platform', 'hackerrank')}",
        "quota": {"limit": cfg.daily_limit, "used_today": ledger.used_on(today),
                  "remaining_today": max(0, cfg.daily_limit - ledger.used_on(today))},
        "plan": plan,
    })
    if failures or blocked or (live and uploaded < len(plan)):
        summary["status"] = "partial"
        return EXIT_PARTIAL
    summary["status"] = "success"
    return EXIT_OK
