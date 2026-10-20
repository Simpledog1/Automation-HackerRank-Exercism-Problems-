# Project rules

## Commit and PR messages
- Never add `Co-Authored-By` trailers, Claude/AI attribution, "Generated with ..." footers,
  or session links to commit messages or pull request descriptions in this repository.
- Commits are authored by the repository owner only. Keep messages to a subject line and,
  when useful, a plain body describing the change.
- A local `commit-msg` hook (`.git/hooks/commit-msg`) strips such lines as a backstop;
  do not bypass it with `--no-verify`.
