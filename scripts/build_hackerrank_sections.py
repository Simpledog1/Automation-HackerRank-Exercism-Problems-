"""One-time helper: save which HackerRank section each challenge belongs to.

HackerRank's export does not say whether a challenge is from the Python section or
Problem Solving, so this reads HackerRank's public challenge lists once and writes
solsync/hackerrank_sections.json. The daily sync only reads that file; it never
calls these (unofficial, undocumented) list endpoints itself.

Re-run it only if you solve challenges that end up as "section unknown":
    python scripts\\build_hackerrank_sections.py
"""
import json
import time
import urllib.request
from pathlib import Path

# repo folder -> HackerRank tracks that belong in it
SECTIONS = {
    "Python": ["python"],
    "Problem Solving (Python)": ["algorithms", "data-structures"],
}
OUT = Path(__file__).resolve().parent.parent / "solsync" / "hackerrank_sections.json"


def fetch(track: str, offset: int) -> dict:
    url = (f"https://www.hackerrank.com/rest/contests/master/tracks/{track}/challenges"
           f"?offset={offset}&limit=50")
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SolutionSync/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except OSError as exc:
            wait = 5 * (attempt + 1)
            print(f"  {track} offset {offset}: {exc}; retrying in {wait}s")
            time.sleep(wait)
    raise SystemExit(f"Could not read the {track} list; nothing was written.")


def track_names(track: str) -> list[str]:
    names, offset = [], 0
    while True:
        data = fetch(track, offset)
        names += [m["name"].strip() for m in data["models"]]
        offset += 50
        if offset >= data["total"]:
            return names
        time.sleep(1)  # be polite


def main() -> None:
    result = {}
    for folder, tracks in SECTIONS.items():
        result[folder] = sorted({n for t in tracks for n in track_names(t)})
        print(f"{folder}: {len(result[folder])} challenges")
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
