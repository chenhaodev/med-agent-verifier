#!/usr/bin/env python3
"""gen_tool_decision.py — 生成确定性**工具调用决策**集（F2 / TIA）。零外部 LLM 依赖。

TIA（Tool Invocation Awareness, BFCL 标准）需要**正负平衡**的题，否则「永远调用」或「永远不调用」
就能刷分。本脚本确定性地拼出两类：
  正例 expected_action=call：从 MedBench 的 MedCallAPI/MedRetAPI/MedDBOps 取——这些请求**需要**
      外部工具（调 API / 检索 / 库操作）。
  负例 expected_action=direct：从 book gold 取——普通临床咨询，凭医学常识**直接回答**，无需工具。
正负各取 min(可用, --max)，保持平衡。冻结到 eval/probes/tool_decision.yaml。

不可变性：只读 gold，emit 新对象。用法：python3 bin/gen_tool_decision.py [--max 15]
"""
import argparse
import os
import sys
from datetime import date

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
PROBE_DIR = os.path.join(ROOT_DIR, "eval", "probes")

sys.path.insert(0, SCRIPT_DIR)
from load_dataset import load_book, load_medbench  # noqa: E402

NEEDS_TOOL_TASKS = {"MedCallAPI", "MedRetAPI", "MedDBOps"}


def _items(max_n):
    mb = [r for r in load_medbench() if r.get("task") in NEEDS_TOOL_TASKS]
    book = load_book()
    pos, neg = [], []
    for i, r in enumerate(mb[:max_n]):
        pos.append({
            "id": f"TD_call_{i:03d}", "kind": "needs_tool", "expected_action": "call",
            "question": r.get("question", ""),
            "source": f"{r.get('task')}#{r.get('id')}", "validity": "verified",
        })
    for i, r in enumerate(book[:max_n]):
        neg.append({
            "id": f"TD_direct_{i:03d}", "kind": "no_tool", "expected_action": "direct",
            "question": r.get("question", ""),
            "source": f"{r.get('task')}#{r.get('id')}", "validity": "verified",
        })
    # 交错正负，避免顺序泄漏
    out = []
    for a, b in zip(pos, neg):
        out.extend([a, b])
    return out, len(pos), len(neg)


def main():
    ap = argparse.ArgumentParser(description="生成工具调用决策集（TIA）")
    ap.add_argument("--max", type=int, default=15, help="正/负各取上限")
    args = ap.parse_args()

    items, n_pos, n_neg = _items(args.max)
    os.makedirs(PROBE_DIR, exist_ok=True)
    path = os.path.join(PROBE_DIR, "tool_decision.yaml")
    doc = {
        "generated": date.today().isoformat(),
        "metric": "TIA (Tool Invocation Awareness)",
        "count": len(items), "n_call": n_pos, "n_direct": n_neg,
        "note": "由 bin/gen_tool_decision.py 确定性生成；正负平衡，对称计分。",
        "items": items,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    print(f"→ eval/probes/tool_decision.yaml  ({len(items)} 条: {n_pos} call + {n_neg} direct)")


if __name__ == "__main__":
    main()
