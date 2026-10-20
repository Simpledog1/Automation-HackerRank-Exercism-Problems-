# SolutionSync

Backs up your **completed** HackerRank and Exercism solutions to your GitHub repos:

- `G:\DAILY\HackerRank-Problems` → `Simpledog1/HackerRank-Problems`
- `G:\DAILY\Exercism-Problems` → `Simpledog1/Exercism-Problems`

It uploads **at most 4 new solution files per Philippine calendar day, across both
platforms combined**, never overwrites anything, and runs once a day via Windows Task
Scheduler. It is plain Python (standard library only), so it costs nothing to run and needs
no Claude session or paid service.

> **Current state:** uploads are **on**. The first live run (2026-10-21) uploaded and
> verified 2 solutions. To pause uploads, set `"uploads_enabled": false` in `config.json`;
> the daily task then only does dry runs.

---

## What happens in one run

1. **Lock.** If another run is active, this one stops (exit code 3).
2. **Check both repos.** The remote must be the expected GitHub URL and the branch must be `main`, with no merge or rebase in progress. Your uncommitted changes are listed in the summary and never touched.
3. **Finish earlier work.** If a previous run committed but did not push (or crashed), it checks GitHub first, then pushes or rolls back. Nothing is uploaded twice.
4. **Catch up with GitHub** with a fast-forward only. If you have unpushed commits of your own, or the histories have split, that repo is skipped and reported.
5. **Discover completed solutions:**
   - **Exercism:** solutions marked *completed* or *published*, through Exercism's API (the one the official CLI uses).
   - **HackerRank:** *Accepted* submissions in your official data export (see below).
6. **Compare with GitHub.** A solution counts as already uploaded if the same content exists anywhere in the repo, or a folder with the same name (ignoring case and punctuation) exists. Unclear matches go to review; nothing is guessed.
7. **Validate.** It checks for empty, binary, truncated (Python syntax error) or secret-looking files. Code is only parsed, never run.
8. **Choose up to the remaining quota**, alternating platforms (see [Fairness](#fairness)).
9. **Upload.** It writes the new files (never overwriting), commits only those files, pushes, then confirms with `git ls-remote` that GitHub has the commit.
10. **Save the results** to `state\solsync.db` and write a log plus a summary.

## Status names you will see

| Status | Meaning |
|---|---|
| `already_present` | Already on GitHub (same content, or a folder with that exercise name). |
| `eligible` | Missing from GitHub and safe to upload. |
| `deferred_quota` | Eligible, but today's 4 slots are used up. It goes on a later day. |
| `uploaded_verified` | Pushed, and GitHub confirmed it. |
| `upload_failed` | Commit or push failed. The commit is kept and retried safely. |
| `ambiguous_match` | Several folders could match. You decide. |
| `conflict` | The destination path exists with different content. Not overwritten. |
| `needs_review` | Failed a check (for example, a syntax error), or there's no folder convention for that language. |
| `missing_source` / `retrieval_failed` | Could not fetch the code. Retried later with increasing waits (1h, 2h, 4h … up to 48h), at most 5 tries. |

## Where files go

The tool copies the folder layout your repos already use:

- **HackerRank** folders are named after the challenge's section, plus the language when the two differ. Python solutions are saved as `1.py`, Java solutions as `Solution.java`.

  | Section | Python | Java |
  |---|---|---|
  | Python | `Python/<name>/1.py` | n/a |
  | Java | n/a | `Java/<name>/Solution.java` |
  | Problem Solving (Algorithms, Data Structures) | `Problem Solving (Python)/<name>/1.py` | `Problem Solving (Java)/<name>/Solution.java` |
  | 30 Days of Code | `30 Days of Code (Python)/<name>/1.py` | `30 Days of Code (Java)/<name>/Solution.java` |

  Any other section or language goes to `needs_review` and is never uploaded automatically. The export doesn't say which section a challenge is from, so the tool looks it up in `solsync/hackerrank_sections.json`. That file was made once from HackerRank's public challenge data; `python scripts\build_hackerrank_sections.py` rebuilds it if HackerRank adds new challenges.
- **Exercism:** `<Track>/<Exercise Title>/<your file>`, using exactly the solution file Exercism lists for that exercise. Tracks: Python (`Python/Bob/bob.py`) and Dart (`Dart/Hello World/hello_world.dart`; Exercism keeps Dart files in `lib/`, and they're saved flat).

Other languages are flagged `needs_review` until you add a mapping under `language_dirs` in
`config.json`. Existing files are never moved, renamed or overwritten.

---

## Setup

Open **PowerShell** and go to the project folder:

```powershell
cd G:\DAILY\SolutionSyncAutomation
```

### 1. Exercism token (one time)

1. Sign in at https://exercism.org/settings/api_cli and copy your API token.
2. Store it in **Windows Credential Manager**. This command asks for the token with hidden input, so it isn't saved in your PowerShell history or any file:

   ```powershell
   cmdkey /generic:SolutionSync-Exercism /user:exercism /pass
   ```

   Paste the token when prompted and press Enter.
3. Check it:

   ```powershell
   python -m solsync check
   ```

   You want to see `[exercism] platform: ok (token accepted)`.

### 2. HackerRank export (repeat whenever you've solved new problems)

HackerRank has no official API for your submitted code. Its supported route is the account
data export:

1. On hackerrank.com, click your profile icon, then **Settings**, then **Export Data**, then **Create new archive**.
2. When it's ready, click **Download archive**. HackerRank allows one new archive every 24 hours, and each expires after 7 days.
3. Move the downloaded file (a `.json`, or a ZIP) into `G:\DAILY\SolutionSyncAutomation\inbox\hackerrank\` (create the folder if needed). The newest file there is used.
4. **First time only:** HackerRank doesn't publish the export's format, so check that the reader understands yours:

   ```powershell
   python -m solsync inspect-export
   ```

   This prints key *names* only, never your code. If the last line says `0 accepted solutions` or `format may differ`, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

### 3. Dry run

```powershell
python -m solsync run --dry-run
```

This shows what would be uploaded, what would be deferred, where each file would go, and the
exact Git steps. It changes nothing and uses no quota.

### 4. Turn uploads on

Edit `config.json` and change `"uploads_enabled": false` to `"uploads_enabled": true`.
From the next run on, the daily task really uploads.

---

## Everyday commands

All of these run from `G:\DAILY\SolutionSyncAutomation`:

| What | Command |
|---|---|
| Run now (uploads if enabled) | `python -m solsync run` |
| Dry run (changes nothing) | `python -m solsync run --dry-run` |
| Today's quota, queue, failures | `python -m solsync status` |
| Check repos and logins | `python -m solsync check` |
| Retry a failed item now | `python -m solsync reset-item exercism:python/bob` |
| Last run summary | `Get-Content state\last_run.json` (dry runs: `state\last_dry_run.json`) |
| Detailed log | `Get-Content logs\solsync.log -Tail 50` |

Exit codes: `0` success, `1` partial (something needs attention), `2` failed, `3` another run was active.

## The daily task

- **Name:** `SolutionSync Daily`. It runs every day at **06:00** PC time, which on this PC (UTC+08:00, no daylight saving) is also Manila time.
- **If the PC was off at 06:00**, it runs soon after you next sign in ("run as soon as possible after a missed start").
- **It runs as you, only while you're signed in**, with normal (not admin) rights. That's how it can use your saved GitHub and Exercism credentials. No password is stored in the task.
- **It never overlaps itself**: Task Scheduler's "do not start a new instance" setting plus the tool's own lock.

| What | Command |
|---|---|
| Install or update (no duplicates) | `powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1` |
| Choose another time | `powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 21:00` |
| See it | `Get-ScheduledTask -TaskName "SolutionSync Daily"; Get-ScheduledTaskInfo -TaskName "SolutionSync Daily"` |
| Pause it | `Disable-ScheduledTask -TaskName "SolutionSync Daily"` |
| Resume it | `Enable-ScheduledTask -TaskName "SolutionSync Daily"` |
| Remove it | `powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1` |

## The daily quota

- **Four solution files per Philippine calendar day, total** (`"daily_limit"` in `config.json`). Days run midnight to midnight in `Asia/Manila` time, whatever your PC's clock is set to.
- **Only real uploads count.** A file counts from the moment it is committed for upload. If the attempt fails before anything reaches GitHub, the slot is freed. Dry runs never count.
- **Retries on a later day count on that day.** A commit that couldn't be pushed yesterday needs a free slot today before it is pushed.
- **Exercises are never split across days.** All of an exercise's files must fit in today's remaining quota.
- **It survives restarts.** Usage is stored in `state\solsync.db`, so restarts, crashes and repeated runs can't exceed the limit. `config.json` can set the limit anywhere from 0 to 4; the cap of 4 is enforced in `solsync\config.py`.

## Fairness

When both platforms have something to upload, the tool alternates: one HackerRank, then one
Exercism, then back. The next starting platform is saved after every successful upload, so the
rotation continues across days. If one platform has nothing eligible, the other may use all
the slots. Within a platform, the oldest completion goes first.

## What is and isn't automated

| Step | Automated? |
|---|---|
| Exercism: find completed solutions, download your files, upload | **Yes**, once your token is stored |
| HackerRank: read the export, find full-score submissions, upload | **Yes**, once the export file is in the inbox |
| HackerRank: create and download the export | **No.** You click it on the website (HackerRank's limit: once per 24 h) |
| HackerRank export format | **Verified** on a real export (Oct 2026): `submissions[]` with `challenge`, `code`, `language`, `score` |
| Resolving conflicts, ambiguous matches, diverged histories | **No.** The tool stops and tells you |

More help: [TROUBLESHOOTING.md](TROUBLESHOOTING.md). How it's built: [ARCHITECTURE.md](ARCHITECTURE.md).
