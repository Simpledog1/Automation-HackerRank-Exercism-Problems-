# Troubleshooting

Start with these two commands. They show what went wrong without changing anything.

```powershell
cd C:\Code\SolutionSync   # wherever you put the SolutionSync folder
python -m solsync status
Get-Content logs\solsync.log -Tail 50
```

## Exercism: "auth problem"

- **`No Exercism token in Windows Credential Manager`:** you haven't stored the token yet. See README → Setup → 1.
- **`Exercism rejected the token (HTTP 401/403)`:** the token expired or was regenerated. Copy the new one from https://exercism.org/settings/api_cli, then replace the stored copy:

  ```powershell
  cmdkey /delete:SolutionSync-Exercism
  cmdkey /generic:SolutionSync-Exercism /user:exercism /pass
  python -m solsync check
  ```

  After an auth error the tool stops calling Exercism for that run, so a bad token never floods the site with requests.

## HackerRank: export problems

- **`no export file in ...inbox\hackerrank`:** download a new export (README → Setup → 2).
- **`export unreadable` / `0 accepted solutions` / `format may differ`:** HackerRank's export layout isn't publicly documented, so the reader looks for records containing code, a challenge name or slug, a language, and the status `Accepted`. Run:

  ```powershell
  python -m solsync inspect-export
  ```

  This lists only key names, such as `doc0.submissions[].code: str`, never your code. If the keys have different names, add them to the lists at the top of `solsync\hackerrank_source.py` (`CODE_KEYS`, `SLUG_KEYS`, `NAME_KEYS`, `LANG_KEYS`, `STATUS_KEYS`), then run the tests and a dry run.

## "local branch has unpushed commits not made by this tool"

You have commits in that repo that you haven't pushed. The tool won't push someone else's
commits, even yours. Push them yourself (`git push` in that repo) or decide what to do with
them. The next run continues normally.

## "local and GitHub histories have diverged"

GitHub has commits you don't have, *and* you have local commits GitHub doesn't have. The tool
won't merge, rebase or force anything. In that repo:

```powershell
git status
git log --oneline --graph --all -15
```

Then pull and resolve as you normally would (`git pull`). If an automation commit is involved,
`python -m solsync status` shows it as an unfinished upload, and the next run continues
once the histories line up again.

## "repository check failed: ... merge --ff-only failed"

GitHub has new commits, but fast-forwarding would overwrite a file you're editing. Commit or
save your edit, then run `git pull` yourself.

## A push failed

You don't need to do anything. The commit stays in your local repo and the ledger remembers it.
The next run checks GitHub first (in case the push did go through), then pushes again. On a
later day it needs a free quota slot before pushing. `python -m solsync status` lists it under
"Unfinished upload".

## `conflict` or `ambiguous_match`

The tool found something at the destination, or several possible matches, and refuses to guess.
Look at the paths in the note. To upload it anyway, move or rename things yourself in the repo.
The tool never does that for you.

## `needs_review`

Read the note:

- **"syntax error":** the code may be truncated, or written for an old Python version.
- **"may contain a secret":** check the file before uploading it by hand.
- **"no folder convention for language":** add the language to `language_dirs` in `config.json`. For example, to use a `Java` folder:

  ```json
  "language_dirs": {"python3": "Python", "java8": "Java"}
  ```

## A failed item keeps waiting

Failed downloads are retried with growing waits and at most 5 attempts. To retry immediately:

```powershell
python -m solsync reset-item exercism:python/bob
```

## The task didn't run

```powershell
Get-ScheduledTaskInfo -TaskName "SolutionSync Daily"
```

- `LastTaskResult` uses the tool's exit codes: `0` ok, `1` partial, `2` failed, `3` already running.
- The task only runs while you're signed in to Windows (that's how it reaches your saved credentials). A missed run happens after you sign in.
- Reinstall it if needed: `powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1`.

## Turning it off completely

```powershell
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
cmdkey /delete:SolutionSync-Exercism
```

Your solution repos aren't affected. You can then delete the `SolutionSyncAutomation` folder.
