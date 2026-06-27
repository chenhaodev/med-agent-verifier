"""theory_screen：分量计分、authority 派生、启发式同族映射、why 不悬空、端到端短名单。

固定 inline fixture，不依赖 ../agent-bench 在位(自包含、可复现)。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import theory_screen as ts  # noqa: E402

W = ts.SCORE_WEIGHTS

# 一个高权威、抗污染的在线榜(医疗·agent)；冠军闭源
FM_MEDBENCH = {
    "domain": ["medical", "clinical-QA"],
    "genre": "online-leaderboard",
    "authority": {"maintainers": ["A", "B"], "institution_count": 500,
                  "update_cadence": "irregular"},
    "methodology": {"contamination_controls": "题答物理隔离;动态评测。"},
    "models_ranked": [
        {"model": "Claude Sonnet 4.5 (agent)", "rank": 1, "axis": "agent", "license": "closed"},
    ],
    "expert_verdict": {"confidence": "high"},
}
# 一个同轴(agent)但非医疗的榜，冠军含本地族(用于验证跨轴迁移命中)
FM_BFCL = {
    "domain": ["function-calling"],
    "genre": "online-leaderboard",
    "authority": {"maintainers": ["UCB"], "institution_count": 1, "update_cadence": "monthly"},
    "methodology": {"contamination_controls": "live 更新。"},
    "models_ranked": [
        {"model": "Qwen3-72B", "rank": 2, "axis": "agent", "license": "open-weights"},
    ],
    "expert_verdict": {"confidence": "medium"},
}


class TestComponents(unittest.TestCase):
    def test_rank_score_decay(self):
        self.assertEqual(ts.rank_score(1), 1.0)
        self.assertAlmostEqual(ts.rank_score(2), 0.8)
        self.assertEqual(ts.rank_score(None), 0.5)   # 缺 rank 保守

    def test_authority_from_real_fields(self):
        # institution_count=500 → inst 满分;maintainers=2/3;irregular cadence
        s = ts.authority_score(FM_MEDBENCH["authority"])
        self.assertTrue(0.0 <= s <= 1.0)
        self.assertGreater(s, ts.authority_score(FM_BFCL["authority"]))  # 500机构 > 1机构

    def test_verdict_and_missing_conservative(self):
        self.assertEqual(ts.verdict_score(FM_MEDBENCH), 1.0)         # high
        self.assertEqual(ts.verdict_score({}), ts.CONSERVATIVE)      # 缺 → 保守

    def test_contam_penalty_waived_when_controls_present(self):
        self.assertEqual(ts.contam_penalty(FM_MEDBENCH), 0.0)        # 有抗污染说明 → 免扣
        bad = dict(FM_MEDBENCH, methodology={"contamination_controls": ""})
        self.assertEqual(ts.contam_penalty(bad), 1.0)               # 在线榜 + 无说明 → 扣

    def test_normalize_family(self):
        self.assertEqual(ts.normalize_family("qwen3.5:2b"), ("qwen", 2.0))
        self.assertEqual(ts.normalize_family("qwen3.5:latest"), ("qwen", None))
        self.assertEqual(ts.normalize_family("Claude Sonnet 4.5 (agent)"), ("claude", None))


class TestMappingAndShortlist(unittest.TestCase):
    def setUp(self):
        self.entries = [("medbench", FM_MEDBENCH), ("bfcl", FM_BFCL)]
        self.champs = ts.collect_champions(self.entries, "medical", "agent", W)

    def test_closed_champion_no_local_family_match(self):
        # 仅闭源 Claude 冠军(只喂 medbench)：本地 qwen 候选无同族 → None
        champs = ts.collect_champions([("medbench", FM_MEDBENCH)], "medical", "agent", W)
        self.assertIsNone(ts.best_match("qwen3.5:latest", champs))

    def test_cross_axis_same_family_transfers(self):
        # bfcl 的 Qwen3-72B(agent 轴)→ 本地 qwen 候选命中(降权迁移)
        match = ts.best_match("qwen3.5:2b", self.champs)
        self.assertIsNotNone(match)
        ev, champ = match
        self.assertEqual(champ["family"], "qwen")
        self.assertLess(champ["relevance"], 1.0)   # 非医疗领域 → relevance<1，先验降权

    def test_build_candidates_testable_and_why_grounded(self):
        pool = [{"name": "qwen3.5:2b", "backend": "ollama", "enabled": True}]
        cands = ts.build_candidates(pool, self.champs, None)
        c = cands[0]
        self.assertTrue(c["testable"])
        self.assertEqual(c["mapped_local"], "qwen3.5:2b")
        # why 回链到真实 entry(bfcl)，不悬空
        self.assertTrue(c["source_entries"])
        self.assertIn("bfcl", c["source_entries"])

    def test_ceilings_are_closed_and_traced(self):
        ceil = ts.build_ceilings(self.champs, "medical", "agent")
        names = [c["model"] for c in ceil]
        self.assertIn("Claude Sonnet 4.5 (agent)", names)
        for c in ceil:
            self.assertFalse(c["testable"])
            self.assertTrue(c["source_entries"])    # 每条都有出处

    def test_neutral_prior_when_no_evidence(self):
        # 只喂闭源医疗榜 → 本地候选拿中性先验，why 注明无证据
        champs = ts.collect_champions([("medbench", FM_MEDBENCH)], "medical", "agent", W)
        pool = [{"name": "qwen3.5:latest", "backend": "ollama", "enabled": True}]
        cands = ts.build_candidates(pool, champs, None)
        self.assertEqual(cands[0]["theory_score"], ts.NEUTRAL_PRIOR)
        self.assertIn("无同族榜单证据", cands[0]["why"][0])

    def test_prefer_license_bonus(self):
        pool = [{"name": "qwen3.5:latest", "backend": "ollama", "enabled": True}]
        base = ts.build_candidates(pool, [], None)[0]["theory_score"]
        boosted = ts.build_candidates(pool, [], "open-weights")[0]["theory_score"]
        self.assertGreater(boosted, base)


if __name__ == "__main__":
    unittest.main()
