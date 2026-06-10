#!/usr/bin/env python3
"""specialty_report.py — Track B 专科覆盖盘点（judge-free，零 API 预算）。

为什么需要它（并回答「为何 repo 不按科室分目录」）：
  本仓是**评测 harness**（验证 Ollama 模型的医学能力），不是知识 agent；按**类型**组织
  bin/(脚本)+eval/(prompt/registry) 是 harness 的正交关注点，正确做法。专科（科室）轴
  存在于**数据与报告**里：Track B 的 domain 即科室，routing_manifest 已按 domain 路由
  （MoA 的「专科→最佳模型」即靠它）。本报告把这条轴显式化为人类可读的两级盘点
  （broad_area ▸ system ▸ domain），对齐 med-agent-internists/psy 的科室分章，
  无需重排目录。

用法：
  python3 bin/specialty_report.py            # 文本盘点
  python3 bin/specialty_report.py --md       # Markdown
  python3 bin/specialty_report.py --thin 5   # 标记 n<5 的薄覆盖专科（默认 5）
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from specialty_map import broad_area, system_of  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_book_records():
    """跑 load_dataset.py --track book，返回归一化记录列表（单一真相源，避免重复解析 gold）。"""
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "bin", "load_dataset.py"), "--track", "book"],
        capture_output=True, text=True, check=True,
    ).stdout
    records = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def build_tree(records):
    """records → {broad_area: {system: {domain: n}}}（不可变构建：逐条累加到新结构）。"""
    tree = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in records:
        dom = r.get("domain") or "未分类"
        src = r.get("gold_source")
        tree[broad_area(dom, src)][system_of(dom, src)][dom] += 1
    return tree


def render(tree, thin, as_md):
    total = sum(n for sys_ in tree.values() for doms in sys_.values() for n in doms.values())
    h = "# Track B 专科覆盖盘点\n" if as_md else "Track B 专科覆盖盘点"
    lines = [h, "" if as_md else "═" * 56]
    lines.append(f"{'共' if not as_md else '**总计**'} {total} 题 · "
                 f"{sum(len(s) for s in tree.values())} 系统 · "
                 f"{sum(len(d) for s in tree.values() for d in s.values())} 专科(domain)")
    lines.append("")
    thin_hits = []
    for area in sorted(tree, key=lambda a: -sum(
            n for d in tree[a].values() for n in d.values())):
        area_n = sum(n for d in tree[area].values() for n in d.values())
        lines.append(f"{'## ' if as_md else ''}▌{area}  （{area_n} 题）")
        for system in sorted(tree[area], key=lambda s: -sum(tree[area][s].values())):
            sys_n = sum(tree[area][system].values())
            doms = ", ".join(f"{d}={n}" for d, n in
                             sorted(tree[area][system].items(), key=lambda kv: -kv[1]))
            mark = "  ⚠薄" if sys_n < thin else ""
            lines.append(f"  {system:<12} n={sys_n:<3} [{doms}]{mark}")
            if sys_n < thin:
                thin_hits.append((area, system, sys_n))
        lines.append("")
    if thin_hits:
        lines.append(f"{'## ' if as_md else ''}⚠ 薄覆盖（n<{thin}，per-domain 分数噪声大，"
                     f"建议看 broad_area 汇总或扩充兄弟 gold）：")
        for area, system, n in thin_hits:
            lines.append(f"  {area} ▸ {system}  (n={n})")
    if not as_md:
        lines.append("═" * 56)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Track B 专科覆盖盘点（judge-free）")
    ap.add_argument("--md", action="store_true", help="Markdown 输出")
    ap.add_argument("--thin", type=int, default=5, help="标记 n<THIN 的薄覆盖系统（默认 5）")
    args = ap.parse_args()
    records = load_book_records()
    tree = build_tree(records)
    print(render(tree, args.thin, args.md))


if __name__ == "__main__":
    main()
