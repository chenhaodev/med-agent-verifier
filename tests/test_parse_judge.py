"""parse_judge.parse — 四维分提取 + 附加 grounding_source / context_awareness。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import parse_judge  # noqa: E402

WELL = ('{"coverage":{"score":9},"accuracy":{"score":8},"safety":{"score":9},'
        '"grounding":{"score":7},"grounding_source":"guideline",'
        '"context_awareness":"appropriate","seeks_clarification":true,"pass":true,"flags":[]}')


class TestParseJudge(unittest.TestCase):
    def test_wellformed_four_dims(self):
        r, ok = parse_judge.parse(WELL)
        self.assertTrue(ok)
        self.assertEqual((r["coverage"], r["accuracy"], r["safety"], r["grounding"]), (9, 8, 9, 7))

    def test_additive_fields(self):
        r, _ = parse_judge.parse(WELL)
        self.assertEqual(r["grounding_source"], "guideline")
        self.assertEqual(r["context_awareness"], "appropriate")
        self.assertIs(r["seeks_clarification"], True)

    def test_invalid_context_label_to_none(self):
        r, ok = parse_judge.parse(WELL.replace('"appropriate"', '"weird"'))
        self.assertTrue(ok)
        self.assertIsNone(r["context_awareness"])

    def test_absent_additive_keeps_four_dims(self):
        flat = '{"coverage":7,"accuracy":7,"safety":8,"grounding":7,"pass":true,"flags":[]}'
        r, ok = parse_judge.parse(flat)
        self.assertTrue(ok)
        self.assertIsNone(r["context_awareness"])
        self.assertEqual(r["coverage"], 7)

    def test_unparseable_marks_not_ok(self):
        r, ok = parse_judge.parse("the judge declined to answer")
        self.assertFalse(ok)
        self.assertFalse(r["ok"])

    def test_regex_fallback_on_broken_json(self):
        # 字符串里有未转义引号让 json.loads 失败 → 逐维正则兜底仍恢复四维
        broken = ('blah {"coverage":{"score":6,"rationale":"他说"好"了"},'
                  '"accuracy":{"score":5},"safety":{"score":7},"grounding":{"score":4')
        r, ok = parse_judge.parse(broken)
        self.assertTrue(ok)
        self.assertEqual((r["coverage"], r["accuracy"], r["safety"], r["grounding"]), (6, 5, 7, 4))


if __name__ == "__main__":
    unittest.main()
