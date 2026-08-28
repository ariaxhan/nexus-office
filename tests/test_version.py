"""The version is one number, and the changelog proves it shipped.

Three fields disagreed before this existed: package.json said 1.0.0, the app
said 0.1.0, and there were no tags at all. Nothing noticed, because a version
nobody asserts is a version that drifts the first busy week.

Everything here reads the repo as it is on disk. No network, no git writes.
"""

import json
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def package_version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def project_field(name: str) -> str:
    for line in (ROOT / "app" / "project.yml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(f"{name}:"):
            return line.split('"')[1]
    raise AssertionError(f"{name} is not in app/project.yml")


class VersionTest(unittest.TestCase):
    def test_every_declaration_agrees(self):
        self.assertEqual(package_version(), project_field("MARKETING_VERSION"),
                         "package.json and the app disagree about the version")

    def test_the_version_is_semver(self):
        self.assertRegex(package_version(), SEMVER)

    def test_the_build_number_is_a_monotonic_integer_not_the_version(self):
        # A build number that tracks the version is not a build number. macOS
        # wants an integer that only ever goes up, and `release` increments it.
        build = project_field("CURRENT_PROJECT_VERSION")
        self.assertTrue(build.isdigit(), f"build number {build!r} is not an integer")

    def test_the_changelog_has_an_entry_for_this_version(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## [{package_version()}]", text,
                      "this version has no changelog entry, so nothing says what shipped")

    def test_a_released_entry_is_never_still_the_generated_draft(self):
        # `changelog` writes a draft and `release` refuses to freeze one. This
        # is the same rule asserted from the other side, on what is already
        # released, so a draft cannot survive a release by any route.
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        released = re.split(r"^## \[Unreleased\]", text, flags=re.M)[-1]
        self.assertNotIn("_Draft, generated", released,
                         "a released changelog entry is still the generated draft")

    def test_release_version_agrees_with_the_script(self):
        out = subprocess.run([str(ROOT / "scripts" / "release-version.sh"), "check"],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
