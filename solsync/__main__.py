"""Command line:  python -m solsync <command>

  run            sync now (uploads only if "uploads_enabled": true in config.json)
  run --dry-run  show what would happen; changes nothing, uses no quota
  status         today's quota, unfinished uploads, items needing attention
  check          check repositories and platform access only
  reset-item KEY let a failed item be retried now (e.g. exercism:python/bob)
  inspect-export show the structure of your HackerRank export (no code or values)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import gitsync, runner
from .config import ConfigError, load_config, manila_day
from .ledger import Ledger


def setup_logging(log_dir) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "solsync.log", maxBytes=1_000_000,
                                       backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console)


def print_summary(s: dict) -> None:
    print(f"Run {s['run_id']}  mode={s['mode']}  status={s.get('status')}  "
          f"Manila day {s['manila_day']}")
    print(f"  started {s['started']}  finished {s.get('finished')}")
    for p, info in s.get("platforms", {}).items():
        print(f"\n[{p}] access: {info['access']}")
        print(f"  repo ok: {info['repo_ok']}" + (
            f"  problems: {'; '.join(info['repo_problems'])}" if info["repo_problems"] else ""))
        if info["your_uncommitted_changes"]:
            print(f"  your uncommitted changes (left untouched): {len(info['your_uncommitted_changes'])}")
        print(f"  records seen: {info['records_seen']}  completed found: {info['completed_found']}")
        for it in info["items"]:
            dest = f" -> {', '.join(it['dest'])}" if it["dest"] else ""
            print(f"   - {it['status']:<24} {it['title']}{dest}" + (f"  ({it['note']})" if it["note"] else ""))
    if "counts" in s:
        print("\nCounts:", ", ".join(f"{k}={v}" for k, v in s["counts"].items()))
        q = s["quota"]
        print(f"Quota: {q['used_today']}/{q['limit']} used today, {q['remaining_today']} remaining")
        print("Fairness:", s["fairness"])
        for item in s["plan"]:
            print(f"  {item['result']}: {item['item']} -> {', '.join(item['files'])}")
            print(f"    git: {item['git']}")


def inspect_export(cfg) -> int:
    """Print the key structure of the newest HackerRank export (names and types only, no values)."""
    from . import hackerrank_source as hr
    path = hr.newest_export(cfg.hackerrank_inbox) if cfg.hackerrank_inbox.exists() else None
    if not path:
        print(f"No export file in {cfg.hackerrank_inbox}")
        return runner.EXIT_PARTIAL
    shapes: dict[str, set[str]] = {}

    def walk(node, prefix: str, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                shapes.setdefault(f"{prefix}.{k}", set()).add(type(v).__name__)
                walk(v, f"{prefix}.{k}", depth + 1)
        elif isinstance(node, list):
            for v in node[:50]:
                walk(v, prefix + "[]", depth + 1)

    for i, doc in enumerate(hr._load_json_docs(path)):
        walk(doc, f"doc{i}", 0)
    print(f"Export: {path.name}")
    for key in sorted(shapes):
        print(f"  {key}: {', '.join(sorted(shapes[key]))}")
    try:
        cands, seen = hr.read_export(path)
        print(f"Reader result: {seen} submission records, {len(cands)} accepted solutions")
    except hr.ExportFormatError as exc:
        print(f"Reader result: {exc}")
    return runner.EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solsync", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("check")
    reset = sub.add_parser("reset-item")
    reset.add_argument("key")
    sub.add_parser("inspect-export")
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return runner.EXIT_FAILED
    setup_logging(cfg.log_dir)

    if args.cmd == "run":
        code, summary = runner.run(cfg, dry_run=args.dry_run)
        print_summary(summary)
        return code

    if args.cmd == "inspect-export":
        return inspect_export(cfg)

    if args.cmd == "check":
        ok = True
        for p, repo in cfg.repos.items():
            problems = gitsync.inspect_repo(repo.path, repo.remote_url, repo.branch)
            if not problems:
                try:
                    gitsync.remote_head(repo.path, repo.branch)
                except gitsync.GitError as exc:
                    problems = [f"cannot reach GitHub: {exc}"]
            print(f"[{p}] repo: {'OK' if not problems else '; '.join(problems)}")
            ok &= not problems
        st = {p: runner.PlatformState() for p in runner.PLATFORMS}
        runner.discover(cfg, st)
        for p, s in st.items():
            print(f"[{p}] platform: {s.access} ({len(s.candidates)} completed found)")
            ok &= s.access.startswith("ok")
        return runner.EXIT_OK if ok else runner.EXIT_PARTIAL

    ledger = Ledger(cfg.state_dir / "solsync.db")
    try:
        if args.cmd == "reset-item":
            found = ledger.reset_item(args.key)
            print("Reset." if found else f"No item named {args.key}")
            return runner.EXIT_OK if found else runner.EXIT_PARTIAL
        today = manila_day()
        print(f"Manila day {today}: {ledger.used_on(today)}/{cfg.daily_limit} files used")
        print(f"Next platform in rotation: {ledger.get_meta('next_platform', 'hackerrank')}")
        print(f"Last successful upload: {ledger.get_meta('last_success', 'never')}")
        for p in runner.PLATFORMS:
            for b in ledger.open_batches(p):
                print(f"Unfinished upload: {b['item_key']} ({b['status']}, day {b['day']})")
        rows = ledger.db.execute(
            "SELECT item_key, status, note, attempts, next_retry FROM items WHERE status NOT IN "
            "('already_present','uploaded_verified') ORDER BY platform, item_key").fetchall()
        for r in rows:
            print(f"  {r['status']:<24} {r['item_key']}  {r['note'] or ''}"
                  + (f"  [attempts {r['attempts']}, retry after {r['next_retry']}]" if r["attempts"] else ""))
        for name in ("last_run.json", "last_dry_run.json"):
            path = cfg.state_dir / name
            if path.exists():
                s = json.loads(path.read_text(encoding="utf-8"))
                print(f"{name}: {s.get('finished')} status={s.get('status')} exit={s.get('exit_code')}")
        return runner.EXIT_OK
    finally:
        ledger.close()


if __name__ == "__main__":
    sys.exit(main())
