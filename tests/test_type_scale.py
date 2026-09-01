"""Every Office text surface goes through the persisted Cmd +/- scale."""

from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OFFICE = ROOT / "app" / "Office"


class TypeScaleSurfaceTest(unittest.TestCase):
    def test_no_view_bypasses_the_shared_font_modifier(self):
        offenders = []
        for source in OFFICE.rglob("*.swift"):
            for number, line in enumerate(source.read_text().splitlines(), 1):
                if ".font(" in line and "return content.font(font)" not in line:
                    offenders.append(f"{source.relative_to(ROOT)}:{number}")
        self.assertEqual(offenders, [], "fixed fonts bypass Cmd +/-: " + ", ".join(offenders))

    def test_unstyled_text_in_the_window_and_menu_gets_the_scaled_default(self):
        app = (OFFICE / "OfficeApp.swift").read_text()
        self.assertGreaterEqual(app.count(".officeFont(size: 13)"), 2)

    def test_the_font_modifier_reads_the_one_root_scale(self):
        theme = (OFFICE / "Views" / "Theme.swift").read_text()
        self.assertIn("@Environment(\\.typeScale) private var scale", theme)
        self.assertIn("size: size * scale", theme)

    def test_symbols_and_layout_geometry_do_not_scale_with_type(self):
        for source in OFFICE.rglob("*.swift"):
            lines = source.read_text().splitlines()
            for number, line in enumerate(lines):
                if "Image(systemName:" in line:
                    nearby = "\n".join(lines[number:number + 4])
                    self.assertIn(".officeSymbol(", nearby,
                                  f"{source.relative_to(ROOT)}:{number + 1}")
                if re.search(r"\bLabel\(", line) and not line.lstrip().startswith("//"):
                    nearby = "\n".join(lines[number:number + 5])
                    self.assertIn(".officeLabel(", nearby,
                                  f"{source.relative_to(ROOT)}:{number + 1}")
            text = "\n".join(lines)
            self.assertIsNone(re.search(r"\.frame\([^\n]*\bscale\b", text),
                              source.relative_to(ROOT))


if __name__ == "__main__":
    unittest.main()
