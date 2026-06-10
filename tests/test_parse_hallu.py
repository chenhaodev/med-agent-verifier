"""parse_hallu.parse — 原子声明级幻觉核查解析；率由计数重算，不信判官算术。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import parse_hallu  # noqa: E402


class TestParseHallu(unittest.TestCase):
    def test_rates_recomputed_from_counts(self):
        # 判官自报 0.99，但应按 claims 数组实际 verdict 重算
        raw = ('{"claims":[{"claim":"a","verdict":"supported"},'
               '{"claim":"b","verdict":"unsupported"},{"claim":"c","verdict":"not_sure"}],'
               '"supported":1,"unsupported":1,"not_sure":1,'
               '"unsupported_rate":0.99,"factual_precision":0.99}')
        r, ok = parse_hallu.parse(raw)
        self.assertTrue(ok)
        self.assertEqual(r["unsupported_rate"], round(1 / 3, 3))
        self.assertEqual(r["factual_precision"], 0.5)

    def test_regex_fallback_counts_verdicts(self):
        bad = ('junk {"claims":[{"claim":"他说"你好"了","verdict":"supported"},'
               '{"claim":"x","verdict":"unsupported"}] trailing')
        r, ok = parse_hallu.parse(bad)
        self.assertTrue(ok)
        self.assertEqual((r["supported"], r["unsupported"]), (1, 1))

    def test_summary_field_fallback(self):
        r, ok = parse_hallu.parse('{"claims":[],"supported":8,"unsupported":2,"not_sure":0}')
        self.assertTrue(ok)
        self.assertEqual(r["unsupported_rate"], 0.2)
        self.assertEqual(r["factual_precision"], 0.8)

    def test_abstention_only_not_penalized(self):
        r, ok = parse_hallu.parse(
            '{"claims":[{"claim":"争议","verdict":"not_sure"}],'
            '"supported":0,"unsupported":0,"not_sure":1}')
        self.assertTrue(ok)
        self.assertEqual(r["unsupported_rate"], 0.0)
        self.assertEqual(r["factual_precision"], 1.0)

    def test_garbage_not_ok(self):
        r, ok = parse_hallu.parse("sorry, refused")
        self.assertFalse(ok)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
