"""End-to-end runs against throwaway Git repos (a local 'GitHub' bare repo + a clone).

Never touches your real repositories.
"""
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from solsync import gitsync, runner
from solsync.config import MANILA, Config, RepoConfig
from solsync.ledger import Ledger
from solsync.matching import sha256
from solsync.models import CONFLICT, UPLOAD_FAILED, Candidate


def sh(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(base: Path, name: str, seed: dict[str, str]) -> tuple[Path, Path]:
    bare, work = base / f"{name}.git", base / name
    sh(base, "init", "--quiet", "--bare", "-b", "main", str(bare))
    sh(base, "clone", "--quiet", str(bare), str(work))
    sh(work, "config", "user.name", "Test")
    sh(work, "config", "user.email", "test@example.com")
    sh(work, "checkout", "--quiet", "-b", "main")
    for rel, text in seed.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text)
    sh(work, "add", ".")
    sh(work, "commit", "--quiet", "-m", "seed")
    sh(work, "push", "--quiet", "-u", "origin", "main")
    return bare, work


def cand(platform, n, files=None):
    return Candidate(platform=platform, item_id=f"python/ex{n}" if platform == "exercism" else f"ex{n}:python3",
                     title=f"Ex {n}", language="python" if platform == "exercism" else "python3",
                     url="https://example.test", method="test", evidence="test",
                     files=files or {"1.py" if platform == "hackerrank" else f"ex{n}.py": f"print({n})\n".encode()})


class GitFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.hr_bare, self.hr = make_repo(base, "hr", {"Python/Old/1.py": "print('old')\n"})
        self.ex_bare, self.ex = make_repo(base, "ex", {"Python/Bob/bob.py": "x = 1\n"})
        proj = base / "proj"
        proj.mkdir()
        self.cfg = Config(
            root=proj, daily_limit=2, uploads_enabled=True,
            repos={"hackerrank": RepoConfig("hackerrank", self.hr, str(self.hr_bare), "main",
                                            {"python3": "Python"}),
                   "exercism": RepoConfig("exercism", self.ex, str(self.ex_bare), "main",
                                          {"python": "Python"})},
            exercism_api_base="https://unused", exercism_credential_target="unused",
            hackerrank_inbox=proj / "inbox")
        self.found = {"hackerrank": [], "exercism": []}
        self.now = datetime(2026, 10, 21, 6, 0, tzinfo=MANILA)

    def tearDown(self):
        self.tmp.cleanup()

    def run_sync(self, dry_run=False):
        def fake_discover(cfg, st):
            for p, items in self.found.items():
                st[p].access = "ok (test)"
                st[p].candidates = [Candidate(**{**c.__dict__, "dest_paths": [], "status": "", "note": ""})
                                    for c in items]
            return None
        with mock.patch.object(runner, "discover", fake_discover), \
                mock.patch.object(runner, "manila_now", return_value=self.now):
            return runner.run(self.cfg, dry_run=dry_run)

    def remote_files(self, bare):
        return set(sh(bare, "ls-tree", "-r", "--name-only", "main").splitlines())

    def ledger(self):
        return Ledger(self.cfg.state_dir / "solsync.db")

    # ------------------------------------------------------------------
    def test_uploads_two_then_defers_then_next_day(self):
        self.found["hackerrank"] = [cand("hackerrank", 1), cand("hackerrank", 2)]
        self.found["exercism"] = [cand("exercism", 3)]
        code, s = self.run_sync()
        self.assertEqual(s["counts"]["uploaded_verified"], 2, s)
        self.assertEqual(code, runner.EXIT_OK)
        self.assertIn("Python/Ex 1/1.py", self.remote_files(self.hr_bare))
        self.assertIn("Python/Ex 3/ex3.py", self.remote_files(self.ex_bare))  # fairness
        # same day again: nothing more
        code, s = self.run_sync()
        self.assertEqual(s["counts"]["uploaded_verified"], 0)
        self.assertEqual(s["counts"]["deferred"], 1)
        self.assertNotIn("Python/Ex 2/1.py", self.remote_files(self.hr_bare))
        # next Manila day: the deferred one goes up, already-uploaded ones are not duplicated
        self.now = datetime(2026, 10, 22, 0, 0, 1, tzinfo=MANILA)
        code, s = self.run_sync()
        self.assertEqual(s["counts"]["uploaded_verified"], 1)
        self.assertIn("Python/Ex 2/1.py", self.remote_files(self.hr_bare))
        self.assertEqual(int(sh(self.hr_bare, "rev-list", "--count", "main")), 3)

    def test_20_dry_run_changes_nothing(self):
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        before = (sh(self.hr, "rev-parse", "HEAD"), sh(self.hr, "status", "--porcelain"),
                  sh(self.hr_bare, "rev-parse", "main"))
        code, s = self.run_sync(dry_run=True)
        self.assertEqual(s["plan"][0]["result"], "would upload")
        after = (sh(self.hr, "rev-parse", "HEAD"), sh(self.hr, "status", "--porcelain"),
                 sh(self.hr_bare, "rev-parse", "main"))
        self.assertEqual(before, after)
        led = self.ledger()
        self.assertEqual(led.used_on("2026-10-21"), 0)
        led.close()

    def test_18_19_only_new_file_is_committed_user_work_untouched(self):
        (self.hr / "Python/Old/1.py").write_text("print('my unsaved edit')\n")
        (self.hr / "notes.txt").write_text("staged by me\n")
        sh(self.hr, "add", "notes.txt")
        (self.hr / "scratch.py").write_text("untracked\n")
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        self.assertEqual(s["counts"]["uploaded_verified"], 1, s)
        changed = sh(self.hr, "show", "--name-status", "--format=", "HEAD").splitlines()
        self.assertEqual(changed, ["A\tPython/Ex 1/1.py"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.hr,
                                capture_output=True, text=True).stdout
        self.assertIn(" M Python/Old/1.py", status)  # still an unstaged edit
        self.assertIn("A  notes.txt", status)
        self.assertIn("?? scratch.py", status)

    def test_07_existing_untracked_file_is_never_overwritten(self):
        target = self.hr / "Python/Ex 1/1.py"
        target.parent.mkdir(parents=True)
        target.write_text("my local version\n")
        c = cand("hackerrank", 1)
        c.dest_paths = ["Python/Ex 1/1.py"]
        led = self.ledger()
        runner.upload(self.cfg, self.cfg.repos["hackerrank"], led, c, "2026-10-21")
        self.assertEqual(c.status, CONFLICT)
        self.assertEqual(target.read_text(), "my local version\n")
        self.assertEqual(led.used_on("2026-10-21"), 0)
        led.close()

    def test_15_failed_push_is_kept_and_retried(self):
        hook = self.hr_bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        self.assertEqual(code, runner.EXIT_PARTIAL)
        self.assertEqual(s["platforms"]["hackerrank"]["items"][0]["status"], UPLOAD_FAILED)
        led = self.ledger()
        self.assertEqual(led.used_on("2026-10-21"), 1)  # a local commit still holds its slot
        led.close()
        hook.unlink()
        code, s = self.run_sync()
        self.assertIn("Python/Ex 1/1.py", self.remote_files(self.hr_bare))
        self.assertEqual(int(sh(self.hr_bare, "rev-list", "--count", "main")), 2)  # no duplicate
        led = self.ledger()
        self.assertEqual(led.open_batches("hackerrank"), [])
        led.close()

    def test_16_push_succeeded_but_state_not_saved(self):
        c = cand("hackerrank", 1)
        rel = "Python/Ex 1/1.py"
        (self.hr / rel).parent.mkdir(parents=True)
        (self.hr / rel).write_bytes(c.files["1.py"])
        sha = gitsync.commit_new_files(self.hr, [rel], "Add Ex 1 solution")
        led = self.ledger()
        batch = led.reserve("2026-10-21", 2, "hackerrank", c.key, [(rel, sha256(c.files["1.py"]))])
        led.set_batch(batch, "committed", commit_sha=sha)
        led.close()
        sh(self.hr, "push", "--quiet", "origin", "main")  # pushed, but "crashed" before recording
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        led = self.ledger()
        rows = led.db.execute("SELECT status FROM uploads").fetchall()
        self.assertEqual([r[0] for r in rows], ["verified"])
        self.assertEqual(led.used_on("2026-10-21"), 1)
        led.close()
        self.assertEqual(int(sh(self.hr_bare, "rev-list", "--count", "main")), 2)

    def test_24_interrupted_before_commit_is_rolled_back(self):
        rel = "Python/Ex 1/1.py"
        data = b"print(1)\n"
        (self.hr / rel).parent.mkdir(parents=True)
        (self.hr / rel).write_bytes(data)
        sh(self.hr, "add", "--", rel)
        led = self.ledger()
        led.reserve("2026-10-21", 2, "hackerrank", "hackerrank:ex1:python3", [(rel, sha256(data))])
        led.close()
        code, s = self.run_sync()
        self.assertFalse((self.hr / rel).exists())
        self.assertEqual(sh(self.hr, "status", "--porcelain"), "")
        led = self.ledger()
        self.assertEqual(led.used_on("2026-10-21"), 0)
        led.close()

    def test_23_wrong_or_missing_remote_blocks_repo(self):
        sh(self.hr, "remote", "set-url", "origin", "https://github.com/someone/else.git")
        problems = gitsync.inspect_repo(self.hr, str(self.hr_bare), "main")
        self.assertTrue(any("expected" in p for p in problems))
        sh(self.hr, "remote", "remove", "origin")
        problems = gitsync.inspect_repo(self.hr, str(self.hr_bare), "main")
        self.assertIn("no 'origin' remote configured", problems)
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        self.assertFalse(s["platforms"]["hackerrank"]["repo_ok"])
        self.assertEqual(s["counts"]["selected"], 0)

    def test_foreign_unpushed_commit_blocks_uploads(self):
        (self.hr / "mine.txt").write_text("x\n")
        sh(self.hr, "add", "mine.txt")
        sh(self.hr, "commit", "--quiet", "-m", "my own work")
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        self.assertFalse(s["platforms"]["hackerrank"]["repo_ok"])
        self.assertNotIn("mine.txt", self.remote_files(self.hr_bare))

    def test_behind_remote_fast_forwards_safely(self):
        other = Path(self.tmp.name) / "other"
        sh(Path(self.tmp.name), "clone", "--quiet", str(self.hr_bare), str(other))
        sh(other, "config", "user.name", "T")
        sh(other, "config", "user.email", "t@example.com")
        (other / "Python/New/1.py").parent.mkdir(parents=True)
        (other / "Python/New/1.py").write_text("print('new')\n")
        sh(other, "add", ".")
        sh(other, "commit", "--quiet", "-m", "elsewhere")
        sh(other, "push", "--quiet", "origin", "main")
        self.found["hackerrank"] = [cand("hackerrank", 1)]
        code, s = self.run_sync()
        self.assertEqual(s["counts"]["uploaded_verified"], 1, s)
        self.assertTrue((self.hr / "Python/New/1.py").exists())


if __name__ == "__main__":
    unittest.main()
