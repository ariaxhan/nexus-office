"""The desk context reader: the Markdown a checkout keeps about itself.

This is the only thing in the office that hands a person the CONTENTS of a file
on this machine, so it is the only thing in the office where a mistake is not a
confusing screen but a leak. Everything here is a test about refusal:

  where it may look    only the checkout `sessions.desk_dir` names, never a
                       path derived from the repo slug
  what it may list     root README Markdown, and `.md` under `_meta`. Nothing
                       else, at any depth, ever
  what it may follow   nothing. A symlink file and a symlink directory are both
                       skipped, so a link planted in `_meta` cannot walk out
  what it may read     a path that exactly matches something it already listed,
                       resolved and proven to still be inside the checkout

A reader that answers `..%2F..%2Fid_rsa` is not a feature with a bug in it, so
the traversal cases assert that no bytes were read at all rather than asserting
on the answer.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

REPO = "acme/checkout-api"


class ContextBase(unittest.TestCase):
    """A real checkout on disk, and `desk_dir` pointed at it.

    `desk_dir` is patched rather than faked around: the whole safety claim of
    this module is that the checkout comes from there and from nowhere else, so
    the tests have to go through the same door the server does.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Resolved, because /var is a symlink to /private/var on a Mac and a
        # containment check that compares unresolved paths would refuse the
        # checkout it was handed.
        self.root = pathlib.Path(self.tmp.name).resolve()
        (self.root / "_meta").mkdir(parents=True)

        import context  # noqa: PLC0415 - reloaded so each test gets it fresh
        self.context = importlib.reload(context)

        self.asked = []
        original = self.context.sessions.desk_dir

        def desk_dir(repo):
            self.asked.append(repo)
            return str(self.root) if repo == REPO else ""

        self.context.sessions.desk_dir = desk_dir
        self.addCleanup(setattr, self.context.sessions, "desk_dir", original)
        self.addCleanup(self.tmp.cleanup)

    # -- fixtures --------------------------------------------------------------

    def write(self, rel: str, text: str = "# hello\n") -> pathlib.Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def index(self, repo: str = REPO):
        code, body = self.context.read(repo)
        self.assertEqual(code, 200, body)
        return body

    def paths(self, repo: str = REPO):
        return [f["path"] for f in self.index(repo)["files"]]


class IndexTest(ContextBase):
    def test_an_empty_checkout_is_an_empty_index_not_an_error(self):
        """A repo with no README and no `_meta` is a real, blameless state. It
        must not read as a repo the office could not open."""
        code, body = self.context.read(REPO)
        self.assertEqual(code, 200)
        self.assertEqual(body["files"], [])
        self.assertEqual(body["repo"], REPO)
        self.assertEqual(body["root"], str(self.root))
        self.assertEqual(body["text"], "")
        self.assertEqual(body["path"], "")

    def test_the_root_readme_is_indexed_and_readable(self):
        self.write("README.md", "# checkout-api\n\nThe door.\n")
        self.assertEqual(self.paths(), ["README.md"])

        code, body = self.context.read(REPO, "README.md")
        self.assertEqual(code, 200)
        self.assertEqual(body["path"], "README.md")
        self.assertIn("The door.", body["text"])
        self.assertEqual(body["title"], "README.md")

    def test_every_markdown_under_meta_is_indexed_at_any_depth(self):
        self.write("_meta/commissions/2026-08-27-context.md")
        self.write("_meta/chronicles/2026/2026-08-ledger.md")
        self.write("_meta/plans/deep/deeper/still.md")
        self.assertEqual(sorted(self.paths()), [
            "_meta/chronicles/2026/2026-08-ledger.md",
            "_meta/commissions/2026-08-27-context.md",
            "_meta/plans/deep/deeper/still.md",
        ])

    def test_a_markdown_extension_is_matched_whatever_its_case(self):
        self.write("_meta/SHOUTED.MD")
        self.write("README.Md")
        self.assertEqual(sorted(self.paths()), ["README.Md", "_meta/SHOUTED.MD"])

    def test_code_and_other_files_are_never_in_the_index(self):
        """The index rule is the whole allow-list. A source file anywhere is
        still a source file, and a `.env` is the reason this is a list of two
        shapes rather than a walk with exclusions."""
        for rel in ("_meta/build.py", "_meta/secrets.env", "_meta/notes.txt",
                    "_meta/data.json", "serve.py", "src/app.ts", ".env",
                    "id_rsa", "db.sqlite", "notes.txt"):
            self.write(rel, "x")
        self.write("README.md")
        self.assertEqual(self.paths(), ["README.md"],
                         "only Markdown, wherever it sits")

    def test_every_markdown_anywhere_in_the_checkout_is_indexed(self):
        """The whole repo, not just the front page and `_meta`: a CHANGELOG, a
        docs folder, a skill file under `.claude` are all things a person opens
        a desk to read."""
        for rel in ("docs/guide.md", "CHANGELOG.md", ".claude/skills/ship/SKILL.md",
                    "src/notes.markdown", "_meta/plans/one.md"):
            self.write(rel)
        self.write("README.md")
        self.assertEqual(self.paths(), [
            "README.md", ".claude/skills/ship/SKILL.md", "CHANGELOG.md",
            "_meta/plans/one.md", "docs/guide.md", "src/notes.markdown"])

    def test_a_folder_that_is_itself_a_checkout_is_not_this_desk_s_context(self):
        """`matra` keeps 2,872 Markdown files under `.claude/worktrees`, each a
        second copy of a file it already lists. A repo inside a repo has its own
        desk; listing it here buries the README of the desk you are looking at.
        A worktree's `.git` is a file and a clone's is a folder; both count."""
        self.write(".claude/worktrees/one/docs/copy.md")
        self.write(".claude/worktrees/one/.git", "gitdir: /elsewhere\n")
        self.write("vendor/dep/README.md")
        self.write("sub/nested/notes.md")
        (pathlib.Path(self.root) / "sub" / "nested" / ".git").mkdir()
        self.write("docs/real.md")
        self.assertEqual(self.paths(), ["docs/real.md"])

    def test_a_readme_that_is_not_markdown_is_not_context(self):
        self.write("README.rst", "not markdown")
        self.write("README.html", "<p>no</p>")
        self.assertEqual(self.paths(), [])

    def test_a_readme_with_no_extension_is_markdown_by_convention(self):
        self.write("README", "# plain\n")
        self.assertEqual(self.paths(), ["README"])

    def test_the_index_is_sorted_the_same_way_every_time(self):
        for rel in ("_meta/z.md", "_meta/a.md", "_meta/m/b.md", "_meta/m/a.md"):
            self.write(rel)
        self.write("README.md")
        self.write("READMEDEV.md")
        self.write("docs/x.md")
        first = self.paths()
        self.assertEqual(first, ["README.md", "READMEDEV.md",
                                 "_meta/a.md", "_meta/m/a.md",
                                 "_meta/m/b.md", "_meta/z.md", "docs/x.md"])
        self.assertEqual(self.paths(), first, "two reads, one order")

    def test_each_entry_carries_what_the_app_draws_and_nothing_more(self):
        self.write("_meta/plans/one.md", "# one\n")
        entry = self.index()["files"][0]
        self.assertEqual(set(entry), {"path", "name", "group", "bytes", "mtime"})
        self.assertEqual(entry["name"], "one.md")
        self.assertEqual(entry["group"], "_meta/plans")
        self.assertEqual(entry["bytes"], len("# one\n"))
        self.assertGreater(entry["mtime"], 0)

    def test_mtime_is_the_file_s_own_and_distinguishes_a_fresh_document(self):
        """The index is folder-ordered, so without this the brief written an hour
        ago is indistinguishable from a note from March."""
        import os
        self.write("_meta/plans/old.md", "# old\n")
        self.write("_meta/plans/new.md", "# new\n")
        old_path = pathlib.Path(self.root) / "_meta/plans/old.md"
        long_ago = old_path.stat().st_mtime - 90 * 24 * 3600
        os.utime(old_path, (long_ago, long_ago))
        by_path = {f["path"]: f for f in self.index()["files"]}
        self.assertLess(by_path["_meta/plans/old.md"]["mtime"],
                        by_path["_meta/plans/new.md"]["mtime"])

    def test_a_root_readme_is_grouped_apart_from_the_meta_tree(self):
        self.write("README.md")
        self.assertEqual(self.index()["files"][0]["group"], "root")


class LimitTest(ContextBase):
    def test_more_than_the_old_file_cap_are_all_indexed(self):
        """A large checkout is still one desk, not its alphabetic first slice."""
        for i in range(2025):
            self.write(f"_meta/many/n{i:04d}.md", "x")
        body = self.index()
        self.assertEqual(len(body["files"]), 2025)
        self.assertFalse(body["capped"])

    def test_scan_count_does_not_truncate_the_index(self):
        # Lower the former boundary so this catches truncation without creating
        # sixty thousand files. Once removed, this assignment has no effect.
        self.context.MAX_SCAN = 3
        for i in range(5):
            self.write(f"docs/{i}.md")
        body = self.index()
        self.assertEqual(len(body["files"]), 5)
        self.assertFalse(body["capped"])

    def test_a_file_that_will_not_open_is_skipped_rather_than_fatal(self):
        self.write("_meta/fine.md", "# fine\n")
        locked = self.write("_meta/locked.md", "# secret\n")
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o644)
        code, body = self.context.read(REPO, "_meta/locked.md")
        self.assertIn(code, (403, 404))
        self.assertNotIn("text", body)
        # ... and the readable one beside it is untouched.
        self.assertEqual(self.context.read(REPO, "_meta/fine.md")[0], 200)


class RefusalTest(ContextBase):
    """The paths that must never reach the filesystem at all.

    Each of these asserts that `desk_dir` was never even consulted where the
    refusal is about the shape of the request, because a check that happens
    after the lookup is a check that has already told you something.
    """

    def setUp(self):
        super().setUp()
        self.write("README.md", "# hello\n")
        self.write("_meta/plan.md", "# plan\n")

    def test_a_traversal_is_refused_without_reading_anything(self):
        for bad in ("../../../etc/passwd", "_meta/../../outside.md",
                    "..", "_meta/../README.md", "./README.md"):
            code, body = self.context.read(REPO, bad)
            self.assertEqual(code, 400, bad)
            self.assertNotIn("text", body, bad)

    def test_an_absolute_path_is_refused(self):
        for bad in ("/etc/passwd", "/", "~/.ssh/id_rsa",
                    str(self.root / "README.md")):
            code, body = self.context.read(REPO, bad)
            self.assertEqual(code, 400, bad)
            self.assertNotIn("text", body, bad)

    def test_a_windows_separator_or_a_null_byte_is_not_a_path_here(self):
        for bad in ("_meta\\plan.md", "README.md\x00.png", "_meta//plan.md"):
            self.assertEqual(self.context.read(REPO, bad)[0], 400, bad)

    def test_a_repo_the_office_cannot_place_is_a_miss_not_a_guess(self):
        with unittest.mock.patch.dict(os.environ,
                                      {"OFFICE_RUNTIME_ROOT": str(self.root)}):
            code, body = self.context.read("acme/not-checked-out")
        self.assertEqual(code, 404)
        self.assertIn("acme/not-checked-out", body["error"])

    def test_a_door_with_no_vault_says_that_rather_than_blaming_the_desk(self):
        """Every desk failing at once is a door started without `--root`, not
        seventy repos that vanished."""
        with unittest.mock.patch.dict(os.environ, {"OFFICE_RUNTIME_ROOT": ""}):
            code, body = self.context.read("acme/not-checked-out")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], self.context.sessions.NO_VAULT)

    def test_a_repo_that_is_not_a_name_with_a_slash_in_it_is_refused(self):
        for bad in ("", "acme", "../../etc", "acme/thing/extra", "acme thing"):
            self.assertEqual(self.context.read(bad)[0], 400, bad)
        self.assertEqual(self.asked, [], "a bad name never reaches the lookup")

    def test_the_checkout_comes_only_from_desk_dir(self):
        self.context.read(REPO, "README.md")
        self.assertEqual(self.asked, [REPO])


class SymlinkTest(ContextBase):
    def test_a_readme_that_is_a_symlink_is_not_the_repos_own_readme(self):
        outside = pathlib.Path(self.tmp.name).parent / "office-context-readme.md"
        outside.write_text("# somewhere else\n", encoding="utf-8")
        self.addCleanup(outside.unlink, True)
        (self.root / "README.md").symlink_to(outside)
        self.assertEqual(self.paths(), [])


class BodyTest(ContextBase):
    def test_a_document_does_not_resend_the_index_the_client_already_has(self):
        self.write("README.md", "# one\n")
        self.write("_meta/two.md", "# two\n")
        self.assertEqual([f["path"] for f in self.index()["files"]],
                         ["README.md", "_meta/two.md"])
        code, body = self.context.read(REPO, "_meta/two.md")
        self.assertEqual(code, 200)
        self.assertEqual(body["files"], [])
        self.assertEqual(body["text"], "# two\n")
        self.assertEqual(body["bytes"], len("# two\n"))

    def test_file_reads_reuse_the_last_verified_index(self):
        self.write("README.md", "# one\n")
        self.write("_meta/two.md", "# two\n")
        with unittest.mock.patch.object(self.context, "index",
                                        wraps=self.context.index) as scan:
            self.assertEqual(self.context.read(REPO)[0], 200)
            self.assertEqual(self.context.read(REPO, "README.md")[0], 200)
            self.assertEqual(self.context.read(REPO, "_meta/two.md")[0], 200)
        self.assertEqual(scan.call_count, 1)

    def test_an_index_refresh_finds_a_file_created_after_the_first_read(self):
        self.write("README.md")
        self.assertEqual(self.paths(), ["README.md"])
        self.write("docs/new.md", "# new\n")
        self.assertEqual(self.paths(), ["README.md", "docs/new.md"])

    def test_direct_open_refreshes_a_stale_index_for_a_new_file(self):
        self.write("README.md")
        self.index()
        self.write("docs/new.md", "# new\n")
        code, body = self.context.read(REPO, "docs/new.md")
        self.assertEqual(code, 200)
        self.assertEqual(body["text"], "# new\n")
        self.assertEqual(body["files"], [])

    def test_bytes_that_are_not_utf8_are_replaced_rather_than_fatal(self):
        (self.root / "_meta" / "odd.md").write_bytes(b"# fine \xff\xfe then\n")
        code, body = self.context.read(REPO, "_meta/odd.md")
        self.assertEqual(code, 200)
        self.assertIn("# fine", body["text"])

    def test_every_answer_carries_the_same_keys_so_a_renderer_never_asks(self):
        self.write("README.md")
        for path in ("", "README.md"):
            body = self.context.read(REPO, path)[1]
            self.assertEqual(set(body), {"repo", "root", "files", "capped",
                                         "path", "title", "text", "bytes"}, path)


class WriteTest(ContextBase):
    def setUp(self):
        super().setUp()
        self.file = self.write("docs/plan.md", "# first\n")

    def payload(self, **changes):
        body = {"repo": REPO, "path": "docs/plan.md",
                "text": "# second\n", "expected": "# first\n"}
        body.update(changes)
        return body

    def test_an_indexed_markdown_file_is_replaced_and_returned(self):
        code, body = self.context.write(self.payload())
        self.assertEqual(code, 200, body)
        self.assertEqual(self.file.read_text(), "# second\n")
        self.assertEqual(body["text"], "# second\n")
        self.assertEqual(body["bytes"], len("# second\n"))
        self.assertEqual([f["path"] for f in body["files"]], ["docs/plan.md"],
                         "a save replaces the whole cached DeskContext")

    def test_an_external_change_is_a_conflict_not_an_overwrite(self):
        self.file.write_text("# changed elsewhere\n")
        code, body = self.context.write(self.payload())
        self.assertEqual(code, 409)
        self.assertIn("changed", body["error"])
        self.assertEqual(self.file.read_text(), "# changed elsewhere\n")

    def test_invalid_fields_and_oversize_text_are_refused(self):
        for body in ({}, self.payload(text=7), self.payload(expected=7),
                     self.payload(text="x" * (self.context.MAX_BYTES + 1))):
            self.assertEqual(self.context.write(body)[0], 400)
        self.assertEqual(self.file.read_text(), "# first\n")

    def test_a_write_preserves_the_file_mode(self):
        self.file.chmod(0o640)
        code, _ = self.context.write(self.payload())
        self.assertEqual(code, 200)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)

    def test_a_change_during_staging_is_not_replaced(self):
        real_fsync = self.context.os.fsync

        def change_then_sync(fd):
            real_fsync(fd)
            self.file.write_text("# changed during save\n")

        with unittest.mock.patch.object(self.context.os, "fsync", change_then_sync):
            code, body = self.context.write(self.payload())
        self.assertEqual(code, 409, body)
        self.assertEqual(self.file.read_text(), "# changed during save\n")


if __name__ == "__main__":
    unittest.main()
