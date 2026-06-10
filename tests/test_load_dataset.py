"""load_dataset：book_sources 优先 vendored、缺则回退兄弟；两路归一化记录的关键字段。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import load_dataset  # noqa: E402


class TestBookSources(unittest.TestCase):
    def test_prefers_vendored_when_present(self):
        # 实仓已有 vendored 快照 → book_sources 应指向 data/book-gold/
        srcs = dict(load_dataset.book_sources())
        for name in ("internists", "psy"):
            self.assertIn(name, srcs)
            self.assertIn(os.path.join("data", "book-gold"), srcs[name])

    def test_falls_back_to_sibling_when_vendored_missing(self):
        orig_dir = load_dataset.VENDORED_BOOK_DIR
        orig_sib = dict(load_dataset.SIBLING_BOOK)
        try:
            with tempfile.TemporaryDirectory() as empty:
                sib_path = os.path.join(empty, "sibling.yaml")
                with open(sib_path, "w", encoding="utf-8") as f:
                    f.write("questions: []\n")
                load_dataset.VENDORED_BOOK_DIR = empty  # 存在但无 .yaml → 视作无 vendored
                load_dataset.SIBLING_BOOK = {"internists": sib_path, "psy": "/nonexistent.yaml"}
                srcs = dict(load_dataset.book_sources())
                self.assertEqual(srcs.get("internists"), sib_path)  # 回退到兄弟
                self.assertNotIn("psy", srcs)  # 兄弟也缺 → 不返回
        finally:
            load_dataset.VENDORED_BOOK_DIR = orig_dir
            load_dataset.SIBLING_BOOK = orig_sib


class TestNormalization(unittest.TestCase):
    def test_medbench_record_shape(self):
        recs = load_dataset.load_medbench()
        self.assertTrue(recs)
        r = recs[0]
        for k in ("track", "task", "id", "gold_type", "question", "reference"):
            self.assertIn(k, r)
        self.assertEqual(r["gold_type"], "reference")

    def test_book_record_shape(self):
        recs = load_dataset.load_book()
        self.assertTrue(recs)
        r = recs[0]
        self.assertEqual(r["gold_type"], "criteria")
        self.assertIn("domain", r)
        self.assertIn("gold_source", r)


if __name__ == "__main__":
    unittest.main()
