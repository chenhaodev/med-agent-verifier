#!/usr/bin/env python3
"""select_subset.py — 生成分层 mini-bench 子集（最难 + 最正交）。零外部依赖、纯确定性。

把两路 gold 的全部记录排成**一个排名**，再切出嵌套的三档：
    mini   ≤30   medium  100   large  全量
三档同源于一个排名，故天然嵌套（mini ⊂ medium ⊂ large），作为可复现的固定基准；
gold 增长后重新生成即可刷新。

「最难」= 零模型的确定性难度启发式：
  Track A(reference)：任务内在难度权重（MedDefend/MedShield/MedReflect/MedCOT 偏难）
                     + 参考答案长度 + 题长。
  Track B(criteria) ：覆盖要点数 + 是否带 must_warn(安全) + 是否带禁止串(幻觉陷阱)
                     + 是否指南题(时效知识)。
「最正交」= 结构化分桶轮询：按 (track,task,domain) 分桶（=能力轴 × 专科轴，两路各自设计上的
  正交维度），桶内按难度降序、桶间按桶内最高难度降序，逐桶轮询取一 —— 先把所有能力/专科
  铺开一遍再回头加深，天然避免把整卷灌成同一个任务/专科。
再叠加**硬覆盖**：MedBench 12 项能力各取最难一条置顶，保证 mini 必含全部能力（12 ≤ 30）。

用法：
  python3 bin/select_subset.py                 # 写 mini/medium/large.yaml
  python3 bin/select_subset.py --no-cover      # 关掉 12 能力硬覆盖，纯按难度+分桶挑
"""
import argparse
import json
import os
import re
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
SUBSET_DIR = os.path.join(ROOT_DIR, "eval", "subsets")

sys.path.insert(0, SCRIPT_DIR)
from load_dataset import load_book, load_medbench  # noqa: E402

# Track A 任务内在难度（0–1）：对抗/安全/反思/推理类更难，工具/分解类较常规。
TASK_WEIGHT = {
    "MedDefend": 1.0, "MedShield": 0.9, "MedReflect": 0.8, "MedCOT": 0.7,
    "MedPathPlan": 0.7, "MedLongConv": 0.65, "MedLongQA": 0.65, "MedCollab": 0.6,
    "MedDecomp": 0.5, "MedCallAPI": 0.5, "MedRetAPI": 0.5, "MedDBOps": 0.5,
}
TIERS = [("mini", 30), ("medium", 100), ("large", None)]  # None = 全量


def _minmax(vals):
    lo, hi = min(vals), max(vals)
    span = hi - lo
    return [(v - lo) / span if span else 0.0 for v in vals]


def _is_guideline(rec):
    q = rec.get("question", "")
    if "GUIDELINE" in str(rec.get("id", "")).upper() or "指南" in q:
        return True
    return bool(re.search(r"20[12]\d\s*年", q))  # 年份（如 2024 年）= 时效性知识，更难


def compute_difficulty(records):
    """逐记录算难度 ∈ [0,1]，长度类特征在各自 track 内 min-max 归一。"""
    a_idx = [i for i, r in enumerate(records) if r["gold_type"] == "reference"]
    b_idx = [i for i, r in enumerate(records) if r["gold_type"] == "criteria"]
    diff = [0.0] * len(records)

    if a_idx:
        reflen = _minmax([len(records[i].get("reference", "")) for i in a_idx])
        qlen = _minmax([len(records[i].get("question", "")) for i in a_idx])
        for k, i in enumerate(a_idx):
            tw = TASK_WEIGHT.get(records[i]["task"], 0.6)
            diff[i] = 0.6 * tw + 0.25 * reflen[k] + 0.15 * qlen[k]

    if b_idx:
        ntopics = _minmax([
            len(records[i].get("criteria", {}).get("expected_topics", [])) for i in b_idx
        ])
        for k, i in enumerate(b_idx):
            crit = records[i].get("criteria", {})
            has_warn = 1.0 if crit.get("must_warn") else 0.0
            trap = crit.get("must_not") or crit.get("patient_must_not_phrases")
            has_trap = 1.0 if trap else 0.0
            guide = 1.0 if _is_guideline(records[i]) else 0.0
            diff[i] = 0.35 * ntopics[k] + 0.25 * has_warn + 0.2 * has_trap + 0.2 * guide

    # 跨 track 再各自 min-max 一遍，保证两路难度在同一量纲可比
    for idx in (a_idx, b_idx):
        if idx:
            norm = _minmax([diff[i] for i in idx])
            for k, i in enumerate(idx):
                diff[i] = norm[k]
    return diff


def coverage_seeds(records, diff):
    """每个 MedBench 任务取最难的一条 → 保证 12 项 Agent 能力在 mini 里全部到场。

    book 有 37 专科 > 30，mini 装不下，靠分桶轮询铺开尽量多专科；而 MedBench 12 能力 ≤ 30，
    是**可保证全覆盖**的，且这 12 项正是项目的头号正交轴，故对它做硬覆盖、其余交给分桶轮询。
    """
    best = {}
    for i, r in enumerate(records):
        if r["track"] != "medbench":
            continue
        t = r["task"]
        if t not in best or diff[i] > diff[best[t]]:
            best[t] = i
    return sorted(best.values(), key=lambda i: (-diff[i], i))


def rank_subset(records, diff, preselected=None):
    """结构化分桶轮询排名（确定性）。

    按 (track,task,domain) 分桶 → 桶内按难度降序 → 桶间按「桶内最高难度」降序 → 逐桶轮询取一。
    难度领跑保证最难的 MedDefend/MedShield 桶与最难的专科桶都早早入选、两路均衡；逐桶轮询保证
    先把所有能力/专科铺开一遍（最正交）再回头加深。`preselected`（如 coverage_seeds）置顶、去重。
    """
    from collections import defaultdict

    pre = list(preselected or [])
    used = set(pre)
    buckets = defaultdict(list)
    for i, r in enumerate(records):
        if i in used:
            continue
        buckets[(r["track"], r["task"], r.get("domain"))].append(i)
    for key in buckets:
        buckets[key].sort(key=lambda i: (-diff[i], i))
    order_keys = sorted(buckets, key=lambda k: (-diff[buckets[k][0]], str(k)))

    order, exhausted = list(pre), False
    while not exhausted:
        exhausted = True
        for k in order_keys:
            if buckets[k]:
                order.append(buckets[k].pop(0))
                exhausted = False
    return order


def write_manifest(name, limit, order, records, diff, meta):
    recs = order if limit is None else order[:limit]
    lines = [
        f"# {name} 子集 — 由 bin/select_subset.py 生成（请勿手改；gold 变动后重跑刷新）",
        f"name: {name}",
        f"description: {'全量' if limit is None else f'最难+最正交 ≤{limit}'}"
        "（嵌套 mini⊂medium⊂large）",
        "selection:",
        f"  method: {meta['method']}",
        f"  generated: {meta['generated']}",
        f"  source_counts: {{medbench: {meta['n_a']}, book: {meta['n_b']}}}",
    ]
    if limit is None:
        # large = 动态全量哨兵：避免 gold 增长后僵化
        lines.append("all: true")
        lines.append("records: []")
    else:
        lines.append("records:")
        for rank, i in enumerate(recs, 1):
            r = records[i]
            rid = json.dumps(r["id"], ensure_ascii=False)
            dom = r.get("domain") if r.get("domain") else "null"
            lines.append(
                f"  - {{track: {r['track']}, task: {r['task']}, id: {rid}, "
                f"domain: {dom}, rank: {rank}, difficulty: {round(diff[i], 3)}}}"
            )
    path = os.path.join(SUBSET_DIR, f"{name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path, recs


def _coverage(recs, records):
    from collections import Counter
    tr = Counter(records[i]["track"] for i in recs)
    tk = Counter(records[i]["task"] for i in recs)
    dm = Counter(records[i]["domain"] for i in recs if records[i].get("domain"))
    return tr, tk, dm


def main():
    ap = argparse.ArgumentParser(description="生成分层 mini-bench 子集（纯确定性，无外部依赖）")
    ap.add_argument("--no-cover", action="store_true",
                    help="关掉「保证 12 项 MedBench 能力全覆盖」的硬置顶（纯按难度+分桶挑）")
    args = ap.parse_args()

    records = load_medbench() + load_book()
    n_a = sum(1 for r in records if r["gold_type"] == "reference")
    n_b = len(records) - n_a
    print(f"载入 {len(records)} 条（medbench {n_a} + book {n_b}）。计算难度…", file=sys.stderr)
    diff = compute_difficulty(records)

    seeds = [] if args.no_cover else coverage_seeds(records, diff)
    if seeds:
        print(f"硬覆盖 {len(seeds)} 项 MedBench 能力（每任务取最难一条）置顶。", file=sys.stderr)
    order = rank_subset(records, diff, preselected=seeds)

    os.makedirs(SUBSET_DIR, exist_ok=True)
    cover = "" if args.no_cover else " + 12能力硬覆盖"
    method = f"structural（难度启发式 + 分桶轮询{cover}）"
    meta = {"method": method, "generated": str(date.today()), "n_a": n_a, "n_b": n_b}

    for name, limit in TIERS:
        path, recs = write_manifest(name, limit, order, records, diff, meta)
        shown = recs if limit is not None else order
        tr, tk, dm = _coverage(shown, records)
        n = len(shown)
        print(f"\n✓ {name:<7} {n:>3} 题 → {os.path.relpath(path, ROOT_DIR)}", file=sys.stderr)
        if limit is not None:
            print(f"    track: {dict(tr)}", file=sys.stderr)
            print(f"    tasks({len(tk)}): {dict(tk)}", file=sys.stderr)
            print(f"    specialties({len(dm)}): {sorted(dm)}", file=sys.stderr)


if __name__ == "__main__":
    main()
