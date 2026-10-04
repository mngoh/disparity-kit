"""Tests for the kit's arithmetic: age bands, weapon classes, ACS band slicing, and an end-to-end run of analyze.py on
a synthetic project with known counts, checking rates, ratios, the race-coding bound and age standardization.

  .venv/bin/python -m pytest tests/      (or: .venv/bin/python -m unittest tests.test_kit)
"""
import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

KIT = pathlib.Path(__file__).resolve().parent.parent / "kit"
sys.path.insert(0, str(KIT))

from analyze import weapon_class  # noqa: E402
from common import AGE_LABELS, age_band  # noqa: E402
from denominators import bands  # noqa: E402


class Units(unittest.TestCase):
    def test_age_band_edges(self):
        self.assertEqual(AGE_LABELS[age_band(0)], "0-4")
        self.assertEqual(AGE_LABELS[age_band(17)], "15-17")
        self.assertEqual(AGE_LABELS[age_band(18)], "18-19")
        self.assertEqual(AGE_LABELS[age_band(44)], "35-44")
        self.assertEqual(AGE_LABELS[age_band(45)], "45-54")
        self.assertEqual(AGE_LABELS[age_band(85)], "85+")
        self.assertEqual(AGE_LABELS[age_band(103)], "85+")

    def test_weapon_class_first_match_then_other(self):
        rules = [("Firearm", ["FIREARM", "HANDGUN"]), ("Knife", ["KNIFE"])]
        self.assertEqual(weapon_class("Handgun", rules), "Firearm")
        self.assertEqual(weapon_class("Knife/Cutting Instrument", rules), "Knife")
        self.assertEqual(weapon_class("Personal Weapons", rules), "Other or unknown")
        self.assertEqual(weapon_class(None, rules), "Other or unknown")

    def test_acs_bands_pick_the_right_variables(self):
        est = {f"B01001B{i:03d}": float(i) for i in range(1, 50)}
        self.assertEqual(bands(est, "B01001B", "M"), [float(i) for i in range(3, 17)])
        self.assertEqual(bands(est, "B01001B", "F"), [float(i) for i in range(18, 32)])


class EndToEnd(unittest.TestCase):
    """Two years, two groups, known counts: Black women 40 victims over 10,000 residents, White women 10 over 20,000."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        rows = []
        for i in range(40):
            rows.append([f"b{i}", f"202{i % 2 + 2}-03-01", "B", "F", 30])
        for i in range(10):
            rows.append([f"w{i}", f"202{i % 2 + 2}-03-01", "W", "F", 30])
        for i in range(5):
            rows.append([f"m{i}", "2022-03-01", "B", "M", 30])
        with open(self.dir / "incidents.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["victim_id", "incident_date", "race_group", "sex", "age"])
            w.writerows(rows)
        cfg = {"title": "t", "place": {"name": "Testville", "census_geoid": "16000US0000000"}, "window": {"start": "2022-01-01", "end": "2023-12-31"},
               "incidents": [{"path": "incidents.csv", "kind": "all"}],
               "columns": {"id": "victim_id", "date": "incident_date", "race": "race_group", "sex": "sex", "age": "age"},
               "race_map": {"B": "Black", "W": "White"}, "groups": ["Black", "White"], "focus": {"group": "Black", "sex": "F", "label": "Black women"}}
        (self.dir / "analysis.json").write_text(json.dumps(cfg))
        (self.dir / "out").mkdir()
        # every woman is 30, so only the 30-34 band (index 7) matters; give it all the residents
        age = lambda n: [0] * 7 + [n] + [0] * 6
        pop = {"city": {"Black": {"F": 10000, "M": 5000}, "White": {"F": 20000, "M": 20000}},
               "city_age": {"Black": {"F": age(10000), "M": age(5000)}, "White": {"F": age(20000), "M": age(20000)}},
               "combo_ratio": {"Black": 1.25}}
        (self.dir / "out/population.json").write_text(json.dumps(pop))
        subprocess.run([sys.executable, str(KIT / "analyze.py"), str(self.dir / "analysis.json")], check=True, capture_output=True, cwd=self.dir)
        self.R = json.loads((self.dir / "out/results.json").read_text())

    def test_rates_are_victims_per_100k_per_year(self):
        self.assertEqual(self.R["years"], 2.0)
        self.assertEqual(self.R["rates"]["Black"]["F"], 200)   # 40 / 10,000 / 2 years * 100,000
        self.assertEqual(self.R["rates"]["White"]["F"], 25)    # 10 / 20,000 / 2 * 100,000
        self.assertEqual(self.R["rates"]["Black"]["M"], 50)    # 5 / 5,000 / 2 * 100,000

    def test_ratio_and_race_coding_bound(self):
        self.assertEqual(self.R["ratios"]["White"], 8.0)
        self.assertEqual(self.R["race_coding_bound"]["ratios_worst_case"]["White"], 6.4)  # 8 / 1.25

    def test_age_standardization_matches_crude_when_all_one_age(self):
        std = self.R["tests"]["age"]["standardized"]
        self.assertEqual(std["Black"], 200)
        self.assertEqual(std["White"], 25)

    def test_counts(self):
        self.assertEqual(self.R["counts"]["total"], 55)
        self.assertEqual(self.R["by_year_counts"]["2022"]["F"], 25)
        self.assertEqual(self.R["by_year_counts"]["2023"]["F"], 25)


if __name__ == "__main__":
    unittest.main()
