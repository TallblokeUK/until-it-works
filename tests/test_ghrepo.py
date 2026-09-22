"""Putting a project on GitHub: what it decides before it does anything.

Nothing here touches the network or gh. The point of this step is that it refuses rather
than guesses, so most of these tests are about what stops it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from helpers import make_repo, sh, tmpdir, write  # noqa: E402

from mp_agent import ghrepo  # noqa: E402


class Planning(unittest.TestCase):
    def test_a_project_with_no_remote_would_be_created(self):
        plan = ghrepo.plan(make_repo({"README.md": "hello"}))
        self.assertEqual(plan["action"], "create")
        self.assertIsNone(plan["remote"])
        self.assertEqual(plan["stoppers"], [])
        self.assertTrue(plan["name"])

    def test_a_project_that_already_has_one_is_pushed_not_created(self):
        repo = make_repo({"README.md": "hello"})
        sh(repo, "git", "remote", "add", "origin", "https://github.com/someone/thing.git")
        plan = ghrepo.plan(repo)
        self.assertEqual(plan["action"], "push")
        self.assertEqual(plan["remote"], "https://github.com/someone/thing.git")

    def test_a_protected_repository_is_never_pushed_to(self):
        from mp_agent import where
        state = tmpdir("mp-protected-")
        where.set_protected(state, ["someone/thing"])
        repo = make_repo({"README.md": "hello"})
        sh(repo, "git", "remote", "add", "origin", "https://github.com/someone/thing.git")
        plan = ghrepo.plan(repo, state_dir=state)
        self.assertTrue(any("protected" in s for s in plan["stoppers"]), plan["stoppers"])

    def test_something_that_is_not_a_repository_stops(self):
        plan = ghrepo.plan(tmpdir("mp-not-a-repo-"))
        self.assertIn("not a git repository", " ".join(plan["stoppers"]))

    def test_untracked_files_are_a_warning_not_a_stopper(self):
        repo = make_repo({"README.md": "hello"})
        write(repo, "notes.txt", "not committed")
        plan = ghrepo.plan(repo)
        self.assertEqual(plan["stoppers"], [])
        self.assertTrue(any("untracked" in w for w in plan["warnings"]), plan["warnings"])


class Secrets(unittest.TestCase):
    def test_a_tracked_credential_stops_it(self):
        repo = make_repo({"README.md": "hello",
                          "config.py": 'TOKEN = "ghp_' + "a" * 36 + '"'})
        self.assertEqual(ghrepo.secrets_in(repo), ["config.py"])
        self.assertIn("credential", " ".join(ghrepo.plan(repo)["stoppers"]))

    def test_an_untracked_one_does_not_reach_github_and_does_not_stop_it(self):
        repo = make_repo({"README.md": "hello"})
        write(repo, ".env", 'ANTHROPIC_API_KEY="sk-ant-' + "b" * 30 + '"')
        self.assertEqual(ghrepo.secrets_in(repo), [])          # untracked: git never sends it
        self.assertEqual(ghrepo.plan(repo)["stoppers"], [])

    def test_ordinary_code_is_not_mistaken_for_a_secret(self):
        repo = make_repo({"README.md": "hello",
                          "app.py": 'key = os.environ["ANTHROPIC_API_KEY"]\ntoken = get_token()\n'})
        self.assertEqual(ghrepo.secrets_in(repo), [])


class Creating(unittest.TestCase):
    def test_the_name_is_checked_before_gh_is_called(self):
        called = []
        url, problem = ghrepo.create(tmpdir(), "not a valid name!", run=lambda *a, **k: called.append(a) or (0, "", ""))
        self.assertIsNone(url)
        self.assertIn("letters, digits", problem)
        self.assertEqual(called, [])

    def test_it_asks_gh_for_a_private_repository_under_the_chosen_owner(self):
        seen = {}

        def run(cmd, cwd=None, timeout=None):
            seen["cmd"] = cmd
            return 0, "https://github.com/Some-Org/thing\n", ""

        url, problem = ghrepo.create(tmpdir(), "thing", owner="Some-Org", run=run)
        self.assertIsNone(problem)
        self.assertEqual(url, "https://github.com/Some-Org/thing")
        self.assertIn("Some-Org/thing", seen["cmd"])
        self.assertIn("--private", seen["cmd"])
        self.assertIn("--push", seen["cmd"])

    def test_public_is_only_when_asked_for(self):
        seen = {}

        def run(cmd, cwd=None, timeout=None):
            seen["cmd"] = cmd
            return 0, "https://github.com/me/thing\n", ""

        ghrepo.create(tmpdir(), "thing", private=False, run=run)
        self.assertIn("--public", seen["cmd"])
        self.assertNotIn("--private", seen["cmd"])

    def test_a_failure_comes_back_as_words_not_an_exception(self):
        url, problem = ghrepo.create(tmpdir(), "thing", run=lambda *a, **k: (1, "", "HTTP 422: name already exists"))
        self.assertIsNone(url)
        self.assertIn("already exists", problem)


if __name__ == "__main__":
    unittest.main()
