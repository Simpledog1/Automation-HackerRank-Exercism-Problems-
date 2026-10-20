"""Matching, quota, fairness, sources and validation (no Git needed)."""
import gzip
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from solsync import exercism_source, hackerrank_source
from solsync.config import manila_day
from solsync.ledger import Ledger
from solsync.lock import AlreadyRunning, RunLock
from solsync.matching import (classify, git_blob_sha, normalize, safe_file_name, safe_name,
                              validate_source)
from solsync.models import (AMBIGUOUS, ELIGIBLE, NEEDS_REVIEW, NEEDS_SOURCE,
                            PRESENT, RETRIEVAL_FAILED, Candidate)
from solsync.runner import PlatformState, fetch_sources, select

PY = {"python": "Python", "python3": "Python"}
REPO = {
    "Python/Bob/bob.py": git_blob_sha(b"def response(x):\n    return 'Whatever.'\n"),
    "Python/Guidos Gorgeous Lasagna/lasagna.py": git_blob_sha(b"EXPECTED = 40\n"),
    "Python/Group(), Groups() & Groupdict()/1.py": git_blob_sha(b"print(1)\n"),
    "README.md": git_blob_sha(b"readme"),
}


def cand(platform="exercism", item="python/pangram", title="Pangram", lang="python", files=None):
    return Candidate(platform=platform, item_id=item, title=title, language=lang, url="u",
                     method="test", evidence="test", files=files)


class MatchingTests(unittest.TestCase):
    def test_01_already_present_by_folder(self):
        c = cand(item="python/bob", title="Bob")
        classify(c, REPO, PY)
        self.assertEqual(c.status, PRESENT)

    def test_02_missing_is_eligible_with_repo_convention(self):
        c = cand(files={"pangram.py": b"def is_pangram(s):\n    return True\n"})
        classify(c, REPO, PY)
        self.assertEqual(c.status, ELIGIBLE)
        self.assertEqual(c.dest_paths, ["Python/Pangram/pangram.py"])

    def test_02c_slug_only_title_goes_to_review(self):
        c = cand("hackerrank", "validate-a-roman-number:python3", "Validate A Roman Number",
                 "python3", files={"1.py": b"print('roman')\n"})
        c.title_from_slug = True
        classify(c, REPO, PY)
        self.assertEqual(c.status, NEEDS_REVIEW)

    def test_02b_missing_without_source_needs_download(self):
        c = cand()
        classify(c, REPO, PY)
        self.assertEqual(c.status, NEEDS_SOURCE)

    def test_03_platforms_are_keyed_separately(self):
        a = cand(platform="exercism", item="python/bob")
        b = cand(platform="hackerrank", item="bob:python3")
        self.assertNotEqual(a.key, b.key)

    def test_04_language_without_convention_needs_review(self):
        c = cand(lang="java", files={"Bob.java": b"class Bob {}"})
        classify(c, REPO, PY)
        self.assertEqual(c.status, NEEDS_REVIEW)

    def test_05_normalized_titles_and_punctuation(self):
        self.assertEqual(normalize("Guido's Gorgeous Lasagna"), normalize("guidos-gorgeous-lasagna"))
        c = cand(item="python/guidos-gorgeous-lasagna", title="Guido's Gorgeous Lasagna")
        classify(c, REPO, PY)
        self.assertEqual(c.status, PRESENT)
        h = cand("hackerrank", "re-group-groups:python3", "Group(), Groups() & Groupdict()", "python3")
        classify(h, REPO, PY)
        self.assertEqual(h.status, PRESENT)

    def test_06_ambiguous_match(self):
        repo = {"Python/Two Sum/1.py": "a", "Python/Two-Sum/1.py": "b"}
        c = cand("hackerrank", "two-sum:python3", "Two Sum", "python3")
        classify(c, repo, PY)
        self.assertEqual(c.status, AMBIGUOUS)

    def test_07_revised_solution_is_not_overwritten(self):
        # Different content in the existing folder = a revision, not a missing solution.
        c = cand(files={"pangram.py": b"x = 1\n"})
        classify(c, {"Python/Pangram/pangram.py": "different-content"}, PY)
        self.assertEqual(c.status, PRESENT)
        self.assertEqual(c.dest_paths, ["Python/Pangram"])
        self.assertIn("not overwritten", c.note)

    def test_08_duplicate_content_elsewhere_is_present(self):
        data = b"def response(x):\n    return 'Whatever.'\n"
        c = cand(item="python/bob-copy", title="Bob Copy", files={"bob.py": data})
        classify(c, REPO, PY)
        self.assertEqual(c.status, PRESENT)
        self.assertIn("Python/Bob/bob.py", c.note)
        crlf = cand(item="python/bob-crlf", title="Bob CRLF",
                    files={"bob.py": data.replace(b"\n", b"\r\n")})
        classify(crlf, REPO, PY)
        self.assertEqual(crlf.status, PRESENT)

    def test_21_unsafe_names(self):
        self.assertIsNone(safe_name("CON"))
        self.assertIsNone(safe_name(".."))
        self.assertEqual(safe_name('a/b:c*?'), "abc")
        self.assertEqual(safe_name("Maximize It!"), "Maximize It!")
        self.assertIsNone(safe_file_name("../evil.py"))
        self.assertIsNone(safe_file_name("sub/dir.py"))
        self.assertEqual(safe_file_name("1.py"), "1.py")
        c = cand(title="..", item="python/x", files={"x.py": b"x=1\n"})
        classify(c, REPO, PY)
        self.assertEqual(c.status, NEEDS_REVIEW)

    def test_25_empty_truncated_binary_secret(self):
        self.assertIn("empty", validate_source({"a.py": b"  \n"}))
        self.assertIn("syntax", validate_source({"a.py": b"def f(:\n"}))
        self.assertIn("binary", validate_source({"a.py": b"x\x00y"}))
        self.assertIn("secret", validate_source({"a.py": b"token = 'abcdefghijklmnop12'\n"}))
        self.assertIsNone(validate_source({"a.py": b"print(input())\n"}))
        c = cand(files={"pangram.py": b""})
        classify(c, REPO, PY)
        self.assertEqual(c.status, NEEDS_REVIEW)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Path(self.tmp.name) / "s.db"
        self.ledger = Ledger(self.db)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_09_two_files_total_across_platforms(self):
        day = "2026-10-21"
        self.assertTrue(self.ledger.reserve(day, 2, "hackerrank", "hackerrank:a", [("p1", "h")]))
        self.assertTrue(self.ledger.reserve(day, 2, "exercism", "exercism:b", [("p2", "h")]))
        self.assertIsNone(self.ledger.reserve(day, 2, "exercism", "exercism:c", [("p3", "h")]))
        self.assertEqual(self.ledger.used_on(day), 2)

    def test_09b_multi_file_exercise_must_fit_entirely(self):
        day = "2026-10-21"
        self.ledger.reserve(day, 2, "hackerrank", "k1", [("p1", "h")])
        self.assertIsNone(self.ledger.reserve(day, 2, "exercism", "k2", [("a", "h"), ("b", "h")]))

    def test_09c_failed_upload_frees_slot_but_committed_counts(self):
        day = "2026-10-21"
        b1 = self.ledger.reserve(day, 2, "hackerrank", "k1", [("p1", "h")])
        b2 = self.ledger.reserve(day, 2, "hackerrank", "k2", [("p2", "h")])
        self.ledger.set_batch(b1, "failed", error="x")
        self.ledger.set_batch(b2, "committed", commit_sha="abc")
        self.assertEqual(self.ledger.used_on(day), 1)

    def test_10_manila_midnight(self):
        before = datetime(2026, 10, 20, 15, 59, 59, tzinfo=timezone.utc)  # 23:59:59 Manila
        after = datetime(2026, 10, 20, 16, 0, 0, tzinfo=timezone.utc)     # 00:00 Manila
        self.assertEqual(manila_day(before), "2026-10-20")
        self.assertEqual(manila_day(after), "2026-10-21")
        self.ledger.reserve("2026-10-20", 2, "x", "k1", [("a", "h"), ("b", "h")])
        self.assertIsNotNone(self.ledger.reserve("2026-10-21", 2, "x", "k2", [("c", "h")]))

    def test_11_quota_persists_across_restart(self):
        self.ledger.reserve("2026-10-21", 2, "x", "k1", [("a", "h"), ("b", "h")])
        self.ledger.close()
        self.ledger = Ledger(self.db)
        self.assertEqual(self.ledger.used_on("2026-10-21"), 2)
        self.assertIsNone(self.ledger.reserve("2026-10-21", 2, "x", "k2", [("c", "h")]))

    def test_13_open_batches_survive_restart(self):
        b = self.ledger.reserve("2026-10-21", 2, "exercism", "exercism:a", [("p", "h")])
        self.ledger.set_batch(b, "committed", commit_sha="deadbeef")
        self.ledger.close()
        self.ledger = Ledger(self.db)
        batches = self.ledger.open_batches("exercism")
        self.assertEqual([x["commit_sha"] for x in batches], ["deadbeef"])

    def test_13b_retry_on_later_day_needs_that_days_quota(self):
        b = self.ledger.reserve("2026-10-20", 2, "x", "k1", [("a", "h")])
        self.ledger.reserve("2026-10-21", 2, "x", "k2", [("b", "h"), ("c", "h")])
        self.assertFalse(self.ledger.move_batch_to_day(b, "2026-10-21", 2))
        self.assertTrue(self.ledger.move_batch_to_day(b, "2026-10-22", 2))


class FairnessTests(unittest.TestCase):
    def mk(self, platform, n, files=1):
        out = []
        for i in range(n):
            c = cand(platform, f"{platform}{i}", f"T{i}")
            c.dest_paths = [f"f{j}" for j in range(files)]
            out.append(c)
        return out

    def test_12_alternates_between_platforms(self):
        sel, dfr = select({"hackerrank": self.mk("hackerrank", 3), "exercism": self.mk("exercism", 3)},
                          2, "exercism")
        self.assertEqual([c.platform for c in sel], ["exercism", "hackerrank"])
        self.assertEqual(len(dfr), 4)

    def test_12b_one_platform_empty_other_uses_both_slots(self):
        sel, _ = select({"hackerrank": self.mk("hackerrank", 3), "exercism": []}, 2, "exercism")
        self.assertEqual([c.platform for c in sel], ["hackerrank", "hackerrank"])

    def test_12c_skips_exercise_that_does_not_fit(self):
        sel, _ = select({"exercism": self.mk("exercism", 1, files=2),
                         "hackerrank": self.mk("hackerrank", 1)}, 1, "exercism")
        self.assertEqual([c.platform for c in sel], ["hackerrank"])

    def test_12d_zero_quota_selects_nothing(self):
        sel, dfr = select({"exercism": self.mk("exercism", 2)}, 0, "exercism")
        self.assertEqual(sel, [])
        self.assertEqual(len(dfr), 2)


class LockTests(unittest.TestCase):
    def test_17_second_run_is_refused(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            path = Path(d) / "run.lock"
            with RunLock(path):
                with self.assertRaises(AlreadyRunning):
                    with RunLock(path):
                        pass
            with RunLock(path):  # released afterwards
                pass


class SourceTests(unittest.TestCase):
    def test_exercism_only_completed_solutions(self):
        sols = [
            {"uuid": "u1", "status": "published", "completed_at": "2026-10-01",
             "exercise": {"slug": "bob", "title": "Bob"}, "track": {"slug": "python"}},
            {"uuid": "u2", "status": "iterated", "exercise": {"slug": "darts", "title": "Darts"},
             "track": {"slug": "python"}},
            {"uuid": "u3", "status": "completed", "exercise": {"slug": "hello", "title": "Hello"},
             "track": {"slug": "rust"}},
        ]
        out = exercism_source.to_candidates(sols)
        self.assertEqual([c.key for c in out], ["exercism:rust/hello", "exercism:python/bob"])

    def test_exercism_downloads_only_solution_files(self):
        client = exercism_source.ExercismClient("https://api", "t", delay=0)
        meta = {"solution": {"user": {"is_requester": True}, "file_download_base_url": "B/",
                             "files": ["bob.py", "bob_test.py", "README.md", ".exercism/config.json"]}}
        files = {"https://api/v1/solutions/u1": json.dumps(meta).encode(),
                 "B/.exercism/config.json": json.dumps({"files": {"solution": ["bob.py"]}}).encode(),
                 "B/bob.py": b"def response(x):\n    pass\n"}
        with mock.patch.object(client, "_get", side_effect=lambda u: files[u]):
            got = client.download_solution_files("u1")
        self.assertEqual(list(got), ["bob.py"])

    def test_exercism_refuses_other_users_solution(self):
        client = exercism_source.ExercismClient("https://api", "t", delay=0)
        meta = {"solution": {"user": {"is_requester": False}, "file_download_base_url": "B/", "files": []}}
        with mock.patch.object(client, "_get", return_value=json.dumps(meta).encode()):
            with self.assertRaises(RuntimeError):
                client.download_solution_files("u1")

    def test_22_auth_failures(self):
        with mock.patch.object(exercism_source, "read_generic_credential", return_value=None):
            with self.assertRaises(exercism_source.AuthError):
                exercism_source.make_client("https://api", "missing")
        import urllib.error
        client = exercism_source.ExercismClient("https://api.exercism.org", "bad", delay=0)
        err = urllib.error.HTTPError("https://api.exercism.org/x", 401, "no", {}, io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(exercism_source.AuthError):
                client.list_solutions()

    def test_token_only_sent_to_exercism(self):
        client = exercism_source.ExercismClient("https://api.exercism.org", "t", delay=0)
        with mock.patch("urllib.request.urlopen") as urlopen:
            for url in ("https://files.example.com/bob.py", "https://evilexercism.org/x",
                        "http://api.exercism.org/x"):
                with self.assertRaises(RuntimeError):
                    client._get(url)
            urlopen.assert_not_called()

    def test_exercism_reads_all_pages_without_total_pages(self):
        client = exercism_source.ExercismClient("https://api.exercism.org", "t", delay=0)
        pages = {1: {"results": [{"uuid": "a"}]}, 2: {"results": [{"uuid": "b"}]}, 3: {"results": []}}
        with mock.patch.object(client, "_json",
                               side_effect=lambda p: pages[int(p.split("page=")[1])]):
            self.assertEqual([s["uuid"] for s in client.list_solutions()], ["a", "b"])

    def test_new_items_are_downloaded_before_old_stuck_ones(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            ledger = Ledger(Path(d) / "s.db")
            old = cand(item="python/old")
            old.status = NEEDS_REVIEW
            ledger.save_item(old)  # seen in an earlier run
            items = []
            for name in ("old", "new"):
                c = cand(item=f"python/{name}")
                c.status, c.source_ref = NEEDS_SOURCE, name
                items.append(c)
            client = mock.Mock()
            client.download_solution_files.return_value = {"x.py": b"x = 1\n"}
            fetch_sources(mock.Mock(max_downloads_per_run=1), ledger, client, items,
                          PlatformState(), save=False)
            client.download_solution_files.assert_called_once_with("new")
            ledger.close()

    def test_14_failed_retrieval_backs_off_then_gives_up(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            ledger = Ledger(Path(d) / "s.db")
            cfg = mock.Mock(max_downloads_per_run=5)
            client = mock.Mock()
            client.download_solution_files.side_effect = exercism_source.TransientError("HTTP 503")
            c = cand()
            c.status, c.source_ref = NEEDS_SOURCE, "u1"
            fetch_sources(cfg, ledger, client, [c], PlatformState(), save=True)
            self.assertEqual(c.status, RETRIEVAL_FAILED)
            self.assertIsNotNone(ledger.item(c.key)["next_retry"])
            # next run before the retry time: no new request
            c2 = cand()
            c2.status, c2.source_ref = NEEDS_SOURCE, "u1"
            fetch_sources(cfg, ledger, client, [c2], PlatformState(), save=True)
            self.assertEqual(client.download_solution_files.call_count, 1)
            ledger.db.execute("UPDATE items SET attempts=5")
            c3 = cand()
            c3.status = NEEDS_SOURCE
            fetch_sources(cfg, ledger, client, [c3], PlatformState(), save=True)
            self.assertIn("gave up", c3.note)
            ledger.close()

    def _zip(self, d, records):
        path = Path(d) / "export.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("data/submissions.json.gz", gzip.compress(json.dumps(records).encode()))
        return path

    def test_hackerrank_export_keeps_newest_accepted_only(self):
        # NOTE: synthetic layout; the real export format is still unverified.
        recs = {"submissions": [
            {"id": 1, "challenge": {"slug": "triangle-quest", "name": "Triangle Quest"},
             "language": "python3", "status": "Accepted", "code": "print(1)\n", "created_at": "2026-01-01"},
            {"id": 2, "challenge": {"slug": "triangle-quest", "name": "Triangle Quest"},
             "language": "python3", "status": "Accepted", "code": "print(2)\n", "created_at": "2026-02-01"},
            {"id": 3, "challenge_slug": "words-score", "language": "python3",
             "status": "Wrong Answer", "code": "x", "created_at": "2026-03-01"},
        ]}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            out, seen = hackerrank_source.read_export(self._zip(d, recs))
        self.assertEqual(seen, 3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].files["1.py"], b"print(2)\n")
        self.assertEqual(out[0].title, "Triangle Quest")

    def test_hackerrank_unrecognized_export_fails_loudly(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            with self.assertRaises(hackerrank_source.ExportFormatError):
                hackerrank_source.read_export(self._zip(d, {"profile": {"name": "x"}}))
            # a country code/name pair must not look like a submission
            with self.assertRaises(hackerrank_source.ExportFormatError):
                hackerrank_source.read_export(
                    self._zip(d, {"country": {"code": "PH", "name": "Philippines"}}))


if __name__ == "__main__":
    unittest.main()
