"""The card contract, and the two ways it is allowed to fail.

Every section carries a `card`: five keys, always, whatever state the source is
in. Two renderers code against exactly that shape (the Mac app now, the phone
page next), and neither of them can ask a question at draw time, so the shape has
to hold in every state rather than in the happy one.

The two tests that matter are the failure paths, because they are the ones that
can take a whole room down:

  a source that raises   already got an error section; it now also gets a card,
                         or the fixture draws with nothing written on it
  a CARD that raises     costs the card and NOTHING else. A summary going wrong
                         must never cost the data it was summarising

`assert_card` is imported by every per-source test file. One checker, so a source
that quietly grows a sixth key or a seventh tone is caught wherever it happens.

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import importlib
import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "client"))

KEYS = {"title", "headline", "needs", "as_of", "facts"}
TONES = {"ok", "warn", "bad", "dim", ""}
FACT_KEYS = {"label", "value", "tone"}
MAX_FACTS = 8


def assert_card(case: unittest.TestCase, card, where: str = "") -> None:
    """The frozen contract, checked. Anything a renderer would have to guess
    about is an assertion here instead."""
    case.assertIsInstance(card, dict, where)
    case.assertEqual(set(card), KEYS, where)

    case.assertIsInstance(card["title"], str, where)
    case.assertTrue(card["title"], f"a card with no title: {where}")

    case.assertIsInstance(card["headline"], str, where)
    case.assertTrue(card["headline"].strip(), f"a card with no headline: {where}")
    # Under 80 so it does not wrap on a card, on a phone, or in a roster row.
    case.assertLess(len(card["headline"]), 80, where)

    # bool is an int in python and would sail through assertIsInstance. A badge
    # that says "True" instead of "3" is the whole reason this is checked.
    case.assertIs(type(card["needs"]), int, where)
    case.assertGreaterEqual(card["needs"], 0, where)

    case.assertIsInstance(card["as_of"], str, where)
    if card["as_of"]:
        case.assertRegex(card["as_of"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", where)

    case.assertIsInstance(card["facts"], list, where)
    case.assertLessEqual(len(card["facts"]), MAX_FACTS, where)
    for fact in card["facts"]:
        case.assertIsInstance(fact, dict, where)
        case.assertEqual(set(fact), FACT_KEYS, where)
        # Already formatted for a person. A renderer that has to decide how to
        # print money is a renderer that prints it differently from the other.
        case.assertIsInstance(fact["label"], str, where)
        case.assertIsInstance(fact["value"], str, where)
        case.assertIn(fact["tone"], TONES, where)


class Boom(RuntimeError):
    pass


class SectionsTest(unittest.TestCase):
    """Nothing is configured, on purpose: every source answers from its own
    not-configured branch, which needs no vault, no network and no subprocess,
    and is the state a fresh machine is actually in."""

    def setUp(self):
        for var in ("OFFICE_RUNTIME_ROOT", "AGENTDB_ROOT", "INTAKE_STATE"):
            self.addCleanup(_restore, var, os.environ.pop(var, None))
        # Nothing listens there, so the library's runtime reads take the
        # normal, fast, runtime-is-closed path instead of a real request.
        self.addCleanup(_restore, "OFFICE_RUNTIME_URL",
                        os.environ.get("OFFICE_RUNTIME_URL"))
        os.environ["OFFICE_RUNTIME_URL"] = "http://127.0.0.1:59998"

        import sections
        self.sections = importlib.reload(sections)

    def by_key(self):
        return {mod.KEY: mod for mod in self.sections.SOURCES}

    def patch(self, key: str, name: str, fn):
        """Swap one function on a source module, and always put it back. The
        modules are shared across the whole test run."""
        mod = self.by_key()[key]
        original = getattr(mod, name)
        setattr(mod, name, fn)
        self.addCleanup(setattr, mod, name, original)
        return mod

    # -- the contract ----------------------------------------------------------

    def test_every_source_carries_a_card(self):
        out = self.sections.read_all()
        self.assertEqual(set(out), {m.KEY for m in self.sections.SOURCES})
        for key, section in out.items():
            self.assertIn("card", section, key)

    def test_every_card_holds_the_shape_both_renderers_code_against(self):
        for key, section in self.sections.read_all().items():
            assert_card(self, section["card"], key)

    def test_a_card_never_replaces_the_data_it_summarises(self):
        out = self.sections.read_all()
        for mod in self.sections.SOURCES:
            section = out[mod.KEY]
            fresh = mod.read()
            for field in fresh:
                self.assertIn(field, section, f"{mod.KEY}.{field}")
            self.assertEqual(set(section) - set(fresh), {"card"}, mod.KEY)

    # -- the two failure paths, which is the whole point -----------------------

    def test_a_card_that_raises_costs_the_card_and_nothing_else(self):
        self.patch("clock", "card", lambda data: (_ for _ in ()).throw(Boom("no")))
        out = self.sections.read_all()

        card = out["clock"]["card"]
        assert_card(self, card, "clock")
        self.assertIn("card failed", card["headline"])
        self.assertIn("Boom", card["headline"])
        self.assertEqual(card["needs"], 0)
        self.assertEqual(card["facts"], [])
        # ... and the section itself is untouched.
        self.assertEqual(set(out["clock"]) - {"card"}, set(self.by_key()["clock"].read()))
        self.assertEqual(out["clock"]["state"], "unconfigured")
        # One bad card, not a bad snapshot.
        for other in ("cost", "library", "mail", "pipeline"):
            self.assertNotIn("card failed", out[other]["card"]["headline"], other)

    def test_a_source_that_raises_is_an_error_section_that_still_has_a_card(self):
        def boom():
            raise Boom("the ledger caught fire")

        self.patch("cost", "read", boom)
        out = self.sections.read_all()

        self.assertEqual(out["cost"]["state"], "error")
        self.assertEqual(out["cost"]["detail"], "Boom: the ledger caught fire")
        card = out["cost"]["card"]
        assert_card(self, card, "cost")
        self.assertEqual(card["title"], "Cost")
        self.assertEqual(card["headline"], "Boom: the ledger caught fire")

    def test_a_source_that_raises_something_enormous_still_fits_a_headline(self):
        def boom():
            raise Boom("x" * 900)

        self.patch("mail", "read", boom)
        card = self.sections.read_all()["mail"]["card"]
        assert_card(self, card, "mail")
        self.assertEqual(card["needs"], 0)


def _restore(var: str, value) -> None:
    if value is None:
        os.environ.pop(var, None)
    else:
        os.environ[var] = value


if __name__ == "__main__":
    unittest.main()


class ClampTest(unittest.TestCase):
    def test_an_unknown_tone_becomes_plain(self):
        card = importlib.import_module("sources._card")
        self.assertEqual(card.fact("x", "1", "PURPLE")["tone"], "")
        self.assertEqual(card.fact("x", "1", "bad")["tone"], "bad")


class BadSourceTest(unittest.TestCase):
    def _run(self, mod):
        sections = importlib.import_module("sections")
        old = sections.SOURCES
        sections.SOURCES = [mod]
        try:
            return sections.read_all()[mod.KEY]
        finally:
            sections.SOURCES = old

    def test_a_source_that_returns_nothing_is_an_error_section_with_a_card(self):
        class Mod:
            KEY = "nothing"
            TITLE = "Nothing"

            @staticmethod
            def read():
                return None

            @staticmethod
            def card(data):
                return {}
        s = self._run(Mod)
        self.assertEqual(s["state"], "error")
        self.assertIn("not a dict", s["detail"])
        self.assertIsInstance(s["card"], dict)

    def test_a_card_that_is_not_a_dict_falls_back(self):
        class Mod:
            KEY = "stringy"
            TITLE = "Stringy"

            @staticmethod
            def read():
                return {"state": "ok"}

            @staticmethod
            def card(data):
                return "nope"
        s = self._run(Mod)
        self.assertIsInstance(s["card"], dict)
        self.assertIn("card failed", s["card"]["headline"])
        self.assertEqual(s["state"], "ok")
