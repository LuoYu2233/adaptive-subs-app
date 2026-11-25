import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_word_difficulty import STAGES, build_table, exam_stages, morphology, write_table


class Tables(dict):
    def get_table(self, name):
        return self[name]


class WordDifficultyTests(unittest.TestCase):
    def setUp(self):
        self.words = {"see": 0, "saw": 1, "leaf": 3, "leave": 0, "go": 0, "dock": None}
        lookups = Tables(lemma_index={"verb": ["see", "leave", "go"], "noun": ["leaf", "dock"]},
            lemma_rules={"verb": [("s", "")], "noun": [("s", "")]},
            lemma_exc={"verb": {"saw": ["see"], "went": ["go"]}, "noun": {"leaves": ["leaf", "leave"]}})
        self.candidates = morphology(lookups, self.words.keys())
        self.frequencies = {"go": 0.2, "went": 0.12, "see": 0.3, "saw": 0.02, "leaves": 0.1,
                            "leaf": 0.01, "leave": 0.025, "dock": 0.015, "docks": 0.005,
                            "n1": 0.01, "unlisted": 0.01}

    def test_conservative_aggregation_preserves_distinct_headwords(self):
        table = build_table(self.frequencies, self.words, self.candidates, 30, 0.2)
        words = table["words"]
        self.assertEqual(words["go"]["rank"], 1)
        self.assertEqual(words["go"]["forms"], 2)
        self.assertEqual(words["see"]["forms"], 1)
        self.assertEqual(words["saw"]["forms"], 1)
        self.assertEqual(words["dock"]["forms"], 2)
        self.assertEqual(table["coverage"], {"headword": 6, "inflection": 2, "nonword": 1, "unmatched": 1, "ambiguous": 1})
        self.assertNotIn("leaves", words)
        self.assertEqual(words["saw"]["rank"], words["dock"]["rank"])
        self.assertEqual(words["dock"]["b_frequency"], words["dock"]["b_initial"])

    def test_rank_and_centers_are_independent_of_output_limit_and_input_order(self):
        all_words = build_table(self.frequencies, self.words, self.candidates, 30, 0.2)
        first = build_table(dict(reversed(self.frequencies.items())), self.words, self.candidates, 2, 0.2)
        self.assertEqual(first["words"], {w: all_words["words"][w] for w in ("go", "see")})
        self.assertEqual(first["stage_centers"], all_words["stage_centers"])
        rank_only = build_table(self.frequencies, self.words, self.candidates, 30, 0.0)
        for row in rank_only["words"].values():
            self.assertAlmostEqual(row["b_frequency"], math.log(row["rank"]))
            self.assertEqual(row["b_frequency"], row["b_initial"])

    def test_exam_masks_use_declared_bit_order_and_earliest_stage(self):
        tags = list(reversed([t for group in STAGES for t in group]))
        mask = (1 << tags.index("cet4")) | (1 << tags.index("gre"))
        data = {"tags": tags, "words": {"fixture": [mask, "测试"], "no-tag": [0, "无"]}}
        self.assertEqual(exam_stages(data)["fixture"], 2)
        self.assertIsNone(exam_stages(data)["no-tag"])
        data["words"]["fixture"][0] = 1 << len(tags)
        with self.assertRaises(ValueError): exam_stages(data)

    def test_failed_serialization_leaves_previous_table_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "词难度.json"
            write_table(out, {"version": 1})
            before = out.read_bytes()
            with self.assertRaises(ValueError):
                write_table(out, {"invalid": float("nan")})
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError): write_table(out, {"version": 2})
            self.assertEqual(out.read_bytes(), before)
            self.assertEqual(json.loads(before)["version"], 1)
            self.assertEqual(list(out.parent.iterdir()), [out])


if __name__ == "__main__":
    unittest.main()
