"""specialty_map + calibrate_hallu.metrics + leaderboard.aggregate 的纯逻辑。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import calibrate_hallu  # noqa: E402
import leaderboard  # noqa: E402
import specialty_map  # noqa: E402

INT = "../med-agent-internists/eval/gold.yaml"
PSY = "../med-agent-psy/eval/gold.yaml"


class TestSpecialtyMap(unittest.TestCase):
    def test_broad_area_from_gold_source(self):
        self.assertEqual(specialty_map.broad_area("cardiology", INT), "内科")
        self.assertEqual(specialty_map.broad_area("depressive", PSY), "精神科")

    def test_broad_area_vendored_path_substring(self):
        # vendored 路径仍含 internist/psy 子串 → 分类不变
        self.assertEqual(specialty_map.broad_area("x", "data/book-gold/internists.yaml"), "内科")
        self.assertEqual(specialty_map.broad_area("x", "data/book-gold/psy.yaml"), "精神科")

    def test_system_of(self):
        self.assertEqual(
            specialty_map.system_of("cardiology", INT), "循环系统")
        self.assertEqual(
            specialty_map.system_of("unknown_dom", INT), "其他内科")


class TestCalibrateMetrics(unittest.TestCase):
    def test_confusion_and_f1(self):
        rows = [
            {"id": "a", "tier": "easy", "gold": "unsupported", "pred": "unsupported"},  # TP
            {"id": "b", "tier": "easy", "gold": "unsupported", "pred": "supported"},    # FN
            {"id": "c", "tier": "easy", "gold": "supported", "pred": "unsupported"},    # FP
            {"id": "d", "tier": "easy", "gold": "supported", "pred": "supported"},      # TN
            {"id": "e", "tier": "easy", "gold": "supported", "pred": "supported"},      # TN
            {"id": "f", "tier": "easy", "gold": "not_sure", "pred": "not_sure"},   # 3class only
        ]
        m = calibrate_hallu.metrics(rows)
        ud = m["unsupported_detection"]
        self.assertEqual((ud["tp"], ud["fp"], ud["fn"], ud["tn"]), (1, 1, 1, 2))
        self.assertEqual((ud["precision"], ud["recall"], ud["f1"]), (0.5, 0.5, 0.5))
        self.assertEqual(m["accuracy_3class"], round(4 / 6, 3))

    def test_predicted_label(self):
        f = calibrate_hallu.predicted_label
        self.assertEqual(f({"supported": 1, "unsupported": 0, "not_sure": 0}), "supported")
        self.assertEqual(f({"supported": 0, "unsupported": 1, "not_sure": 0}), "unsupported")
        self.assertEqual(f({"supported": 0, "unsupported": 0, "not_sure": 1}), "not_sure")
        self.assertIsNone(f(None))


def _book_row(rid, dom, src, total, hallu=None):
    r = {"track": "book", "task": "x", "id": rid, "domain": dom, "gold_type": "criteria",
         "gold_source": src, "grounding_source": "book", "model_response": "x" * 200,
         "scores": {"coverage": 9, "accuracy": 9, "safety": 9, "grounding": 9, "total": total},
         "pass": True, "hallucinated": False}
    if hallu:
        r["hallu"] = hallu
    return r


class TestLeaderboardAggregate(unittest.TestCase):
    def test_specialty_rollup_groups_by_broad_area(self):
        def h(s, u):
            return {"n_claims": s + u, "supported": s, "unsupported": u, "not_sure": 0,
                    "unsupported_rate": round(u / (s + u), 3),
                    "factual_precision": round(s / (s + u), 3)}
        by = {"m": [
            _book_row("a", "cardiology", INT, 36, h(9, 1)),
            _book_row("b", "respiratory", INT, 30, h(8, 2)),
            _book_row("c", "depressive", PSY, 20, h(5, 5)),
        ]}
        agg, _ = leaderboard.aggregate(by)
        ru = agg["specialty_rollup"]
        self.assertEqual(ru["内科"]["m"]["n"], 2)
        self.assertEqual(ru["内科"]["m"]["unsupported_metric"], "claim")
        self.assertEqual(ru["内科"]["m"]["unsupported_rate"], round(3 / 20, 3))
        self.assertEqual(ru["精神科"]["m"]["n"], 1)
        self.assertIn("cardiology", agg["specialty"])  # per-domain preserved

    def test_ctx_appropriate_rate_diagnostic(self):
        rows = [_book_row(str(i), "cardiology", INT, 36) for i in range(3)]
        rows[0]["context_awareness"] = "appropriate"
        rows[1]["context_awareness"] = "appropriate"
        rows[2]["context_awareness"] = "overconfident"
        _, diag = leaderboard.aggregate({"m": rows})
        self.assertEqual(diag["m"]["ctx_appropriate_rate"], round(2 / 3, 3))
        self.assertEqual(diag["m"]["ctx_n"], 3)


if __name__ == "__main__":
    unittest.main()
