# SolutionSync

**Automatically back up your solved HackerRank and Exercism problems to GitHub, a few per day.**

You solve problems on HackerRank and Exercism. SolutionSync finds the ones you have
*actually completed*, checks which are not on GitHub yet, and uploads them to your own
solution repositories, a few files each day, so your GitHub shows steady progress
instead of one giant dump. It runs by itself once a day using Windows Task Scheduler.

- ✅ Uploads only **completed** solutions (Exercism: marked complete; HackerRank: full score)
- ✅ Uses **your real submitted code**; it never writes or changes solutions
- ✅ **Never overwrites or duplicates** anything already in your repos
- ✅ **Daily limit** (default 4 files per day, shared across both platforms)
- ✅ Free: plain Python, **no extra packages**, no paid services, no AI

> **Windows only.** It uses Windows Task Scheduler and Windows Credential Manager.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install](#2-install)
3. [Configure](#3-configure)
4. [Connect Exercism](#4-connect-exercism)
5. [Connect HackerRank](#5-connect-hackerrank)
6. [Try it safely (dry run)](#6-try-it-safely-dry-run)
7. [Turn it on](#7-turn-it-on)
8. [Everyday use](#8-everyday-use)
9. [Where files go](#9-where-files-go)
10. [How it keeps your work safe](#10-how-it-keeps-your-work-safe)
11. [Status names](#11-status-names)
12. [Uninstall](#12-uninstall)

---

## 1. What you need

| Thing | Notes |
|---|---|
| Windows 10 or 11 | |
| [Python](https://www.python.org/downloads/) 3.10 or newer | Check with `python --version`. Developed and tested with 3.15. |
| [Git for Windows](https://git-scm.com/download/win) | Includes Git Credential Manager, which remembers your GitHub login. |
| A GitHub account | |
| One GitHub repo for HackerRank solutions | For example `HackerRank-Problems`. It can be empty. |
| One GitHub repo for Exercism solutions | For example `Exercism-Problems`. It can be empty. |
| HackerRank and/or Exercism accounts | You can use just one platform; see [step 3](#3-configure). |

All commands below are typed into **PowerShell**. In VS Code: **Terminal → New
Terminal**; the prompt starts with `PS`.

## 2. Install

**a) Download SolutionSync** to a folder of your choice, for example `C:\Code`:

```powershell
cd C:\Code
git clone https://github.com/Simpledog1/Automation-HackerRank-Exercism-Problems-.git SolutionSync
cd SolutionSync
```

**b) Clone your two solution repos** (use your own GitHub username and repo names).
Keep them *outside* the SolutionSync folder:

```powershell
cd C:\Code
git clone https://github.com/YOUR-USERNAME/HackerRank-Problems.git
git clone https://github.com/YOUR-USERNAME/Exercism-Problems.git
```

**c) Make sure Git can push to them.** This asks GitHub for permission without sending anything:

```powershell
git -C C:\Code\HackerRank-Problems push --dry-run
```

If a GitHub sign-in window appears, sign in once; Git remembers it. If the repo is empty,
make one normal commit and push first (for example add a README on github.com, then `git pull`).

## 3. Configure

Open `config.json` in the SolutionSync folder and replace the example values with yours:

```json
{
  "daily_limit": 4,
  "uploads_enabled": false,
  "max_downloads_per_run": 10,
  "repos": {
    "hackerrank": {
      "path": "C:/Code/HackerRank-Problems",
      "remote_url": "https://github.com/YOUR-USERNAME/HackerRank-Problems.git",
      "branch": "main"
    },
    "exercism": {
      "path": "C:/Code/Exercism-Problems",
      "remote_url": "https://github.com/YOUR-USERNAME/Exercism-Problems.git",
      "branch": "main",
      "language_dirs": {"python": "Python", "dart": "Dart"}
    }
  },
  "exercism": {
    "api_base": "https://api.exercism.org",
    "credential_target": "SolutionSync-Exercism"
  },
  "hackerrank": {
    "inbox": "inbox/hackerrank"
  }
}
```

| Setting | What it means |
|---|---|
| `daily_limit` | Most new solution files uploaded per day, both platforms combined (0–4). |
| `uploads_enabled` | Keep `false` until your dry run looks right. While `false`, every run is a dry run. |
| `max_downloads_per_run` | How many Exercism solutions to download per run (keeps requests polite). |
| `path` | Where you cloned that solution repo. Use forward slashes `/`. |
| `remote_url` | The repo's GitHub URL. SolutionSync refuses to push anywhere else. |
| `branch` | The branch to upload to, usually `main`. |
| `language_dirs` | Exercism track → top folder in your repo. Add a line per track you use, e.g. `"javascript": "JavaScript"`. Tracks not listed are skipped and shown for review. |
| `credential_target` | Name of the Windows Credential Manager entry that holds your Exercism token (step 4). |
| `inbox` | Folder where you drop your HackerRank export (step 5). |

> **Time zone:** the "day" for the daily limit is counted in Philippine time (UTC+8). To use
> your own, edit the top of `solsync\config.py`: change `ZoneInfo("Asia/Manila")` to your
> zone (e.g. `ZoneInfo("America/New_York")`, which on Windows also needs
> `python -m pip install tzdata`) and change the fallback `timedelta(hours=8)` to your UTC offset.

**Only using one platform?** Still fill in both repos (they are checked), and just skip
step 4 or step 5. The unused platform is skipped with a message such as `No Exercism token`
or `no export file`, and runs report "partial" (exit code 1) because of it. Uploads for the
other platform work normally.

## 4. Connect Exercism

SolutionSync reads your Exercism solutions with your personal API token, stored safely in
Windows Credential Manager (never in a file or in the code).

1. Sign in at <https://exercism.org/settings/api_cli> and copy your **API token**.
2. In PowerShell run the command below, paste the token at the prompt (input is hidden), and press Enter:

   ```powershell
   cmdkey /generic:SolutionSync-Exercism /user:exercism /pass
   ```

3. Check it:

   ```powershell
   python -m solsync check
   ```

   You should see `[exercism] platform: ok (token accepted)`.

> Never paste your token into chats, issues, or code. If you ever do, regenerate it on
> Exercism and store the new one with the same command.

## 5. Connect HackerRank

HackerRank has no official API for your submitted code, but it lets you download all your
data. SolutionSync reads that file. Repeat this whenever you have solved new problems:

1. On hackerrank.com click your profile icon → **Settings** → **Export Data** → **Create new archive**.
2. When it is ready, click **Download**. (HackerRank allows one new archive per 24 hours.)
3. Put the downloaded file (`.json` or `.zip`) into the `inbox\hackerrank` folder inside SolutionSync. Create the folder if it doesn't exist. The newest file there is used.
4. Check that it was understood:

   ```powershell
   python -m solsync inspect-export
   ```

   This shows only the *structure* of the file (never your code). The last line should say how
   many solutions were found.

A HackerRank solution counts as completed when it scored **full marks (1.0)**.

## 6. Try it safely (dry run)

```powershell
python -m solsync run --dry-run
```

A dry run changes nothing and uses none of the daily limit. It lists every solution it
found and what it would do with it:

```
[hackerrank] access: ok (export my_data.json)
  records seen: 231  completed found: 137
   - eligible                 Plus Minus -> Problem Solving (Python)/Plus Minus/1.py
   - deferred_quota           Text Wrap -> Python/Text Wrap/1.py  (daily quota reached; ...)
Quota: 0/4 used today, 4 remaining
  would upload: hackerrank:plus-minus:python -> Problem Solving (Python)/Plus Minus/1.py
```

Solutions already in your repo are counted (`already_present=` in the Counts line) but not listed.

Check that the folders and file names look right for your repos before continuing.

## 7. Turn it on

**a) Allow uploads:** in `config.json` set `"uploads_enabled": true`.

**b) Do one run yourself and look at the result on GitHub:**

```powershell
python -m solsync run
```

**c) Schedule it daily** (default 06:00; choose any time with `-Time`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Time 06:00
```

The task runs as you, only while you are signed in to Windows, without admin rights, so it
can use your saved GitHub and Exercism logins. If your PC was off at that time, it runs soon
after you next sign in. Running the install command again just updates the task.

That's it. From now on it uploads up to `daily_limit` files every day.

## 8. Everyday use

Run these from the SolutionSync folder. You never *need* to; the daily task does the work.

| What | Command |
|---|---|
| Quick summary | `python -m solsync status \| Select-Object -First 4` |
| Everything waiting / needing attention | `python -m solsync status` |
| Dry run | `python -m solsync run --dry-run` |
| Upload now (counts toward today's limit) | `python -m solsync run` |
| Check repos and logins | `python -m solsync check` |
| Recent log | `Get-Content logs\solsync.log -Tail 30` |
| Retry a failed item now | `python -m solsync reset-item exercism:python/bob` |
| Pause the daily task | `Disable-ScheduledTask -TaskName "SolutionSync Daily"` |
| Resume it | `Enable-ScheduledTask -TaskName "SolutionSync Daily"` |

**Solved new problems?** Exercism ones are found automatically. For HackerRank, download a
fresh export into `inbox\hackerrank` (step 5).

**Change the daily amount:** edit `"daily_limit"` in `config.json` (0 pauses uploads).

## 9. Where files go

SolutionSync follows a fixed folder layout and never moves or renames existing files.

**Exercism:** `<Track folder>/<Exercise title>/<your solution file>`

```
Exercism-Problems/
  Python/
    Bob/bob.py
    Grains/grains.py
  Dart/
    Hello World/hello_world.dart
```

The track folder comes from `language_dirs` in `config.json`. Only the file(s) Exercism
marks as *your solution* are copied, never tests or READMEs.

**HackerRank:** `<Section (Language)>/<Challenge name>/<file>`. The language is left out
when it matches the section name. Python solutions are saved as `1.py`, Java as `Solution.java`.

| HackerRank section | Python | Java |
|---|---|---|
| Python | `Python/<name>/1.py` | n/a |
| Java | n/a | `Java/<name>/Solution.java` |
| Problem Solving (Algorithms, Data Structures) | `Problem Solving (Python)/<name>/1.py` | `Problem Solving (Java)/<name>/Solution.java` |
| 30 Days of Code | `30 Days of Code (Python)/<name>/1.py` | `30 Days of Code (Java)/<name>/Solution.java` |

HackerRank's export doesn't say which section a challenge belongs to, so SolutionSync looks
it up in `solsync\hackerrank_sections.json` (built from HackerRank's public challenge lists).
Challenges from other sections or in other languages are not uploaded; they are listed for
you to place by hand. If HackerRank adds new challenges, refresh the list with:

```powershell
python scripts\build_hackerrank_sections.py
```

**Already have solutions in your repo?** SolutionSync recognises them if the same code is
anywhere in the repo, or a folder with the same exercise name exists (ignoring case and
punctuation, so `Guidos Gorgeous Lasagna` matches `Guido's Gorgeous Lasagna`). It will not
upload them again.

## 10. How it keeps your work safe

- **Never overwrites.** If the destination already exists, the item is skipped and reported.
- **Commits only its own new files.** Your other edits, staged or not, are left exactly as they are.
- **Pushes only to the URL in `config.json`.** Never force-pushes, never resets, rebases or deletes.
- **Stops instead of guessing.** If your branch has unpushed commits of your own, or local and GitHub have split apart, that repo is skipped until you sort it out.
- **Counts an upload only when GitHub confirms it.** If a push fails or the PC crashes, the next run checks GitHub first, then finishes or retries, so nothing is lost or uploaded twice.
- **Never runs your code.** Files are checked for being empty, binary, truncated (Python syntax), or containing something that looks like a password or key.
- **Keeps secrets out of files and logs.** The Exercism token lives only in Windows Credential Manager.
- **One run at a time.** A lock stops two runs from overlapping and going over the daily limit.

**Daily limit details:** a "day" is midnight to midnight (see the time zone note in step 3).
A file counts once it is committed for upload; if nothing reached GitHub, its slot is
freed. An exercise with several files is never split across days. When both platforms have
work, they take turns: one HackerRank, one Exercism, and so on.

## 11. Status names

| Status | Meaning |
|---|---|
| `eligible` | Missing from GitHub and ready to upload. |
| `deferred_quota` | Ready, but today's limit is used up. It goes on a later day. |
| `uploaded_verified` | Uploaded, and GitHub confirmed it. |
| `already_present` | Already in your repo. Nothing to do. |
| `needs_source` | Exercism solution not downloaded yet (a few are fetched each run). |
| `needs_review` | Not uploaded: unsupported section/language, or a failed check (e.g. syntax error). |
| `conflict` / `ambiguous_match` | Something already sits at that path, or several folders could match. Not touched; you decide. |
| `upload_failed` | Commit or push failed. Kept and retried safely on the next run. |
| `retrieval_failed` / `missing_source` | Couldn't fetch the code. Retried later with growing waits. |

Exit codes (useful in Task Scheduler): `0` success, `1` something needs attention,
`2` failed, `3` another run was already active.

## 12. Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
cmdkey /delete:SolutionSync-Exercism
```

Then delete the SolutionSync folder. Your solution repos are not affected.

---

More help: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · How it works inside: [ARCHITECTURE.md](ARCHITECTURE.md)
