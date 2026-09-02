"""One search box over every desk.

The claim under test is a narrow one, and it is a safety claim before it is a
feature claim: **search can see exactly what the Context pane can see, and
nothing else.** It matches against `context.index`, so a file that reader would
refuse to list is invisible here for free. These tests hold that line, and then
hold the three shapes of answer a person actually types:

  a name      `glossary` finds the file called that, ahead of one that merely
              mentions the word
  a folder    a directory name in the middle of a path is a match
  a path      an absolute, `~`, vault-relative, or `owner/repo:path` spelling
              goes straight to the file rather than into a ranked list

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))


class SearchBase(unittest.TestCase):
    """Two checkouts on disk, and the module pointed at the vault holding them.

    Real directories rather than a faked index: the whole point of matching
    against `context.index` is that it is the same walk the Context pane does,
    and a stubbed index would test a different function.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vault = pathlib.Path(self.tmp.name).resolve()

        import search  # noqa: PLC0415 - reloaded so each test gets it fresh
        self.search = importlib.reload(search)

        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.vault)
        self.addCleanup(os.environ.pop, "OFFICE_RUNTIME_ROOT", None)

        self.desks = {}
        self.search.desks = lambda: dict(self.desks)
        self.search.forget()

    def desk(self, repo: str, name: str = "") -> pathlib.Path:
        root = self.vault / (name or repo.split("/")[-1])
        root.mkdir(parents=True, exist_ok=True)
        self.desks[repo] = str(root)
        self.search.forget()
        return root

    def write(self, root: pathlib.Path, rel: str, text: str = "# hello\n"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        self.search.forget()
        return p

    def run_(self, q):
        return self.search.run(q)

    def paths(self, q):
        return [(r["kind"], r["repo"], r["path"]) for r in self.run_(q)["results"]]


class RefusalTest(SearchBase):
    def test_a_short_query_searches_nothing(self):
        """One character is every file in the vault, which is not a search."""
        root = self.desk("acme/api")
        self.write(root, "README.md", "a\n")
        self.assertEqual(self.run_("a")["results"], [])
        self.assertEqual(self.run_("")["results"], [])

    def test_a_door_with_no_vault_says_so_rather_than_answering_empty(self):
        os.environ.pop("OFFICE_RUNTIME_ROOT", None)
        body = self.run_("glossary")
        self.assertEqual(body["results"], [])
        self.assertTrue(body["said"])

    def test_only_markdown_the_context_reader_lists_is_searchable(self):
        """The allow-list is `context.index` and this file adds nothing to it.

        A secret whose name and contents both match the query is still not a
        result, because it is not Markdown and was never in the index.
        """
        root = self.desk("acme/api")
        self.write(root, ".env", "SECRET_TOKEN=glossary\n")
        self.write(root, "keys/glossary.pem", "glossary\n")
        self.write(root, "src/glossary.py", "# glossary\n")
        self.assertEqual(self.paths("glossary"), [])

    def test_a_traversing_path_finds_nothing_rather_than_leaving_the_vault(self):
        root = self.desk("acme/api")
        self.write(root, "README.md", "hi\n")
        outside = pathlib.Path(self.tmp.name).parent / "elsewhere.md"
        for spelling in ("../../etc/passwd", "/etc/passwd", str(outside),
                         "api/../../../etc/hosts"):
            self.assertEqual(self.paths(spelling), [], spelling)

    def test_vendored_documentation_is_not_this_desks_documentation(self):
        """Nine thousand plugin READMEs must never bury the one real file."""
        root = self.desk("acme/api")
        self.write(root, ".claude/plugins/cache/kernel/docs/deploy.md", "deploy\n")
        self.write(root, "docs/deploy.md", "deploy\n")
        self.assertEqual(self.paths("deploy"), [("name", "acme/api", "docs/deploy.md")])


class NameTest(SearchBase):
    def test_a_filename_beats_a_mention_of_the_same_word(self):
        root = self.desk("acme/api")
        self.write(root, "notes/glossary.md", "nothing in here\n")
        self.write(root, "notes/other.md", "see the glossary for more\n")
        kinds = [r["kind"] for r in self.run_("glossary")["results"]]
        self.assertEqual(kinds[0], "name")
        self.assertIn("text", kinds)

    def test_a_folder_name_is_a_match(self):
        root = self.desk("acme/api")
        self.write(root, "reference/runtime/notes.md", "nothing\n")
        self.assertIn(("name", "acme/api", "reference/runtime/notes.md"),
                      self.paths("runtime"))

    def test_a_word_that_starts_a_word_beats_one_buried_in_another(self):
        """`shoot` must answer with `app-shoot.md`, not `troubleshooting.md`.

        Both are true substring matches and neither starts with the word, so
        only the word-boundary rule separates them. Six hundred files named
        `troubleshooting.md` is the real shape of this vault.
        """
        root = self.desk("acme/api")
        # The one a person means sorts LAST by path and is written first, so
        # neither of the tiebreakers under the rank can be what puts it on top:
        # only the rank itself can.
        self.write(root, "docs/zz-app-shoot.md", "x\n")
        for n in range(5):
            self.write(root, f"docs/troubleshooting-{n}.md", "x\n")
        first = self.run_("shoot")["results"][0]
        self.assertEqual(first["path"], "docs/zz-app-shoot.md")

    def test_search_crosses_every_desk_at_once(self):
        one = self.desk("acme/api")
        two = self.desk("acme/web")
        self.write(one, "deploy.md", "x\n")
        self.write(two, "deploy.md", "x\n")
        repos = {r["repo"] for r in self.run_("deploy")["results"]}
        self.assertEqual(repos, {"acme/api", "acme/web"})


class GotoTest(SearchBase):
    def test_a_vault_relative_path_goes_straight_there(self):
        root = self.desk("acme/api", name="api")
        self.write(root, "_meta/plans/ship.md", "x\n")
        got = self.run_("api/_meta/plans/ship.md")["results"]
        self.assertEqual(got[0]["kind"], "goto")
        self.assertEqual((got[0]["repo"], got[0]["path"]),
                         ("acme/api", "_meta/plans/ship.md"))

    def test_an_absolute_path_goes_straight_there(self):
        root = self.desk("acme/api")
        p = self.write(root, "_meta/plans/ship.md", "x\n")
        got = self.run_(str(p))["results"]
        self.assertEqual(got[0]["kind"], "goto")
        self.assertEqual(got[0]["path"], "_meta/plans/ship.md")

    def test_a_home_relative_path_goes_straight_there(self):
        """`~/...` is what a person has in the clipboard, and it is the one
        spelling the vault-relative join cannot accidentally rescue.

        This one needs its vault under `$HOME` to be able to write the query at
        all, so it makes its own rather than reusing the shared temporary.
        """
        home = pathlib.Path.home()
        holder = tempfile.TemporaryDirectory(dir=str(home))
        self.addCleanup(holder.cleanup)
        self.vault = pathlib.Path(holder.name).resolve()
        os.environ["OFFICE_RUNTIME_ROOT"] = str(self.vault)
        root = self.desk("acme/api")
        p = self.write(root, "_meta/plans/ship.md", "x\n")
        spelling = "~/" + str(p.relative_to(home.resolve()))
        got = self.run_(spelling)["results"]
        self.assertEqual(got[0]["kind"], "goto")
        self.assertEqual(got[0]["path"], "_meta/plans/ship.md")

    def test_a_desk_relative_path_goes_straight_there(self):
        root = self.desk("acme/api")
        self.write(root, "_meta/plans/ship.md", "x\n")
        got = self.run_("_meta/plans/ship.md")["results"]
        self.assertEqual(got[0]["kind"], "goto")

    def test_owner_repo_colon_path_goes_straight_there(self):
        one = self.desk("acme/api")
        two = self.desk("acme/web")
        self.write(one, "docs/ship.md", "x\n")
        self.write(two, "docs/ship.md", "x\n")
        got = self.run_("acme/web:docs/ship.md")["results"]
        self.assertEqual((got[0]["kind"], got[0]["repo"]), ("goto", "acme/web"))

    def test_a_path_that_is_not_there_is_not_an_error(self):
        self.desk("acme/api")
        body = self.run_("_meta/plans/nothing-here.md")
        self.assertEqual(body["results"], [])
        self.assertEqual(body["counts"]["goto"], 0)


class TextTest(SearchBase):
    def test_the_matched_line_comes_back_with_its_number(self):
        root = self.desk("acme/api")
        self.write(root, "notes.md", "one\ntwo\nthe permission gate is sharp\n")
        hit = self.run_("permission gate")["results"][0]
        self.assertEqual(hit["kind"], "text")
        self.assertEqual(hit["line"], 3)
        self.assertEqual(hit["snippet"], "the permission gate is sharp")

    def test_matching_ignores_case(self):
        root = self.desk("acme/api")
        self.write(root, "notes.md", "The Permission Gate\n")
        self.assertEqual(len(self.run_("permission gate")["results"]), 1)

    def test_a_long_line_is_quoted_around_the_match_not_from_the_start(self):
        root = self.desk("acme/api")
        self.write(root, "notes.md", ("padding " * 60) + "NEEDLE here\n")
        hit = self.run_("needle")["results"][0]
        self.assertIn("NEEDLE", hit["snippet"])
        self.assertLessEqual(len(hit["snippet"]), self.search.SNIPPET + 2)

    def test_a_file_that_is_a_name_hit_is_not_repeated_as_a_text_hit(self):
        root = self.desk("acme/api")
        self.write(root, "glossary.md", "glossary glossary\n")
        got = self.run_("glossary")["results"]
        self.assertEqual(len(got), 1)

    def test_a_byte_that_is_not_utf8_does_not_lose_the_file(self):
        root = self.desk("acme/api")
        (root / "notes.md").write_bytes(b"\xff\xfe needle in here\n")
        self.search.forget()
        self.assertEqual(len(self.run_("needle")["results"]), 1)

    def test_an_unreadable_desk_costs_one_desk_not_the_search(self):
        one = self.desk("acme/api")
        self.write(one, "notes.md", "needle\n")
        self.desks["acme/gone"] = str(self.vault / "not-checked-out-any-more")
        self.search.forget()
        self.assertEqual(len(self.run_("needle")["results"]), 1)


if __name__ == "__main__":
    unittest.main()
