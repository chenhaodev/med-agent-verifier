"""model_pool：defaults 合并、schema 校验、enabled 过滤、实仓池文件健全。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import model_pool  # noqa: E402


def _write_pool(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name


class TestLoadPool(unittest.TestCase):
    def test_defaults_merge_and_override(self):
        path = _write_pool(
            "defaults: {backend: ollama, think: 'off'}\n"
            "models:\n"
            "  - name: a\n"
            "  - name: b\n"
            "    backend: siliconflow\n"
            "    think: ''\n"
            "    enabled: false\n"
        )
        entries = model_pool.load_pool(path)
        self.assertEqual(entries[0],
                         {"name": "a", "backend": "ollama", "think": "off",
                          "enabled": True, "notes": ""})
        self.assertEqual(entries[1]["backend"], "siliconflow")
        self.assertEqual(entries[1]["think"], "")     # 条目显式空覆盖 defaults
        self.assertFalse(entries[1]["enabled"])

    def test_invalid_backend_rejected(self):
        path = _write_pool("models:\n  - name: a\n    backend: nosuch\n")
        with self.assertRaises(ValueError):
            model_pool.load_pool(path)

    def test_invalid_think_rejected(self):
        path = _write_pool("models:\n  - name: a\n    think: maybe\n")
        with self.assertRaises(ValueError):
            model_pool.load_pool(path)

    def test_missing_name_rejected(self):
        path = _write_pool("models:\n  - backend: ollama\n")
        with self.assertRaises(ValueError):
            model_pool.load_pool(path)

    def test_duplicate_entry_rejected(self):
        path = _write_pool("models:\n  - name: a\n  - name: a\n")
        with self.assertRaises(ValueError):
            model_pool.load_pool(path)

    def test_repo_pool_file_loads_with_enabled_models(self):
        entries = model_pool.load_pool()           # 实仓 eval/model_pool.yaml
        enabled = [e for e in entries if e["enabled"]]
        self.assertGreaterEqual(len(enabled), 1)
        names = {e["name"] for e in entries}
        # 视觉/embedding 模型按约定永不入池（CLAUDE.md：garbage text scores）
        for banned in ("minicpm", "bge-", "nomic-embed"):
            self.assertFalse(any(banned in n for n in names), banned)


if __name__ == "__main__":
    unittest.main()
