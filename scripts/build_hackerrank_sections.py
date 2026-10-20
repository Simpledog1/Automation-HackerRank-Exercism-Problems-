"""One-time helper: save which HackerRank section each challenge belongs to.

HackerRank's export does not say which section a challenge is from, so this reads
HackerRank's public challenge data once and writes solsync/hackerrank_sections.json.
The daily sync only reads that file; it never calls these (unofficial, undocumented)
endpoints itself.

Re-run it only if you solve challenges that end up as "section unknown":
    python scripts\\build_hackerrank_sections.py
"""
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://www.hackerrank.com/rest/contests/master"
# Section name -> where its challenges are listed.
TRACKS = {
    "Python": ["python"],
    "Problem Solving": ["algorithms", "data-structures"],
    "Java": ["java"],
}
# 30 Days of Code (Tutorials) has no list endpoint, so each original challenge is looked
# up by its address name; only answers that HackerRank files under this chapter count.
THIRTY_DAYS_CHAPTER = "30-days-of-code"
THIRTY_DAYS_SLUGS = [
    "30-hello-world", "30-data-types", "30-operators", "30-conditional-statements",
    "30-class-vs-instance", "30-loops", "30-review-loop", "30-arrays",
    "30-dictionaries-and-maps", "30-recursion", "30-binary-numbers", "30-2d-arrays",
    "30-inheritance", "30-abstract-classes", "30-scope", "30-linked-list",
    "30-exceptions-string-to-integer", "30-more-exceptions", "30-queues-stacks",
    "30-interfaces", "30-sorting", "30-generics", "30-binary-search-trees",
    "30-tree-level-order-traversal", "30-linked-list-deletion",
    "30-running-time-and-complexity", "30-nested-logic", "30-testing", "30-regex-patterns",
    "30-bitwise-and",
]
OUT = Path(__file__).resolve().parent.parent / "solsync" / "hackerrank_sections.json"


def get(url: str) -> dict | None:
    """JSON from url, None on 404, retrying network hiccups politely."""
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SolutionSync/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            err = exc
        except OSError as exc:
            err = exc
        wait = 5 * (attempt + 1)
        print(f"  {url.split('/master/')[1][:50]}: {err}; retrying in {wait}s")
        time.sleep(wait)
    raise SystemExit("HackerRank did not answer; nothing was written.")


def track_names(track: str) -> list[str]:
    names, offset = [], 0
    while True:
        data = get(f"{BASE}/tracks/{track}/challenges?offset={offset}&limit=50")
        names += [m["name"].strip() for m in data["models"]]
        offset += 50
        if offset >= data["total"]:
            return names
        time.sleep(1)  # be polite


def thirty_days_names() -> list[str]:
    names = []
    for slug in THIRTY_DAYS_SLUGS:
        model = (get(f"{BASE}/challenges/{slug}") or {}).get("model") or {}
        if (model.get("track") or {}).get("slug") == THIRTY_DAYS_CHAPTER:
            names.append(model["name"].strip())
        else:
            print(f"  skipped {slug}: not found under {THIRTY_DAYS_CHAPTER}")
        time.sleep(1)
    return names


def main() -> None:
    result = {section: sorted({n for t in tracks for n in track_names(t)})
              for section, tracks in TRACKS.items()}
    result["30 Days of Code"] = sorted(thirty_days_names())
    for section, names in result.items():
        print(f"{section}: {len(names)} challenges")
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
