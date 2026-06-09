#!/usr/bin/env python3
"""select_subset.py — 生成分层 mini-bench 子集（最难 + 最正交）。

把两路 gold 的全部记录排成**一个 MMR 排名**，再切出嵌套的三档：
    mini   ≤30   medium  100   large  全量
「最难」由**零模型的确定性难度启发式**给出；「最正交」由**句向量 + farthest-first/MMR**
贪心给出（避免选到语义冗余的题）。三档同源于一个排名，故天然嵌套（mini ⊂ medium ⊂ large），
作为可复现的固定基准；gold 增长后重新生成即可刷新。

难度信号（model-free）：
  Track A(reference)：任务内在难度权重（MedDefend/MedShield/MedReflect/MedCOT 偏难）
                     + 参考答案长度 + 题长。
  Track B(criteria) ：覆盖要点数 + 是否带 must_warn(安全) + 是否带禁止串(幻觉陷阱)
                     + 是否指南题(时效知识)。
正交性：nomic-embed-text 句向量（官方 Ollama，bin/call_embed.sh，带缓存）→ 单位化 → 余弦距离。
MMR 贪心：每步选 argmax[ λ·难度 + (1-λ)·到已选集的最小距离 ]，λ 默认 0.5（--lambda 调）。

用法：
  python3 bin/select_subset.py                 # 默认 embed-mmr，写 mini/medium/large.yaml
  python3 bin/select_subset.py --no-embed      # 退化为结构化分桶轮询（无需 Ollama）
  python3 bin/select_subset.py --lambda 0.7    # 更偏「难」；0.3 更偏「正交」
"""
import argparse
import json
import math
import os
import subprocess
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
    rid = str(rec.get("id", ""))
    if "GUIDELINE" in rid.upper():
        return True
    if "指南" in q:
        return True
    # 年份（如 2024）通常意味着时效性知识，更难
    import re
    return bool(re.search(r"20[12]\d\s*年", q))


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
    if a_idx:
        na = _minmax([diff[i] for i in a_idx])
        for k, i in enumerate(a_idx):
            diff[i] = na[k]
    if b_idx:
        nb = _minmax([diff[i] for i in b_idx])
        for k, i in enumerate(b_idx):
            diff[i] = nb[k]
    return diff


def _unit(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


# nomic-embed-text 上下文约 2048 token；长题（MedLongQA/MedPathPlan 可达 3500+ 字）会超限报 500。
# 截断到安全字符预算再嵌入——用于「正交性」挑选，前 ~1500 字已足够代表语义，无损选择质量。
EMBED_CHAR_BUDGET = 1500


def _embed_one(text):
    """单条嵌入，失败返回 None。逐级截断容忍超长输入。"""
    for budget in (EMBED_CHAR_BUDGET, 600, 200):
        snippet = text[:budget]
        out = subprocess.run(
            [os.path.join(SCRIPT_DIR, "call_embed.sh")],
            input=snippet.encode("utf-8"), capture_output=True, timeout=320,
        )
        if out.returncode == 0:
            return _unit(json.loads(out.stdout.decode()))
    return None


def embed_records(records):
    """逐题取句向量（call_embed.sh，带缓存）。

    单条失败不致命：先逐级截断重试；仍失败则记 None、占位。仅当**首条**即失败
    （多半 Ollama 没起或缺 nomic-embed-text）才整体返回 None → 调用方退化为结构化。
    失败占位最后用成功向量的质心填充（中性，不人为拉远/拉近）。
    """
    vecs, failed = [], []
    for i, r in enumerate(records):
        try:
            v = _embed_one(r.get("question", ""))
        except Exception as e:  # noqa: BLE001
            print(f"警告：嵌入异常（{r['track']}/{r['id']}）：{e}", file=sys.stderr)
            v = None
        if v is None:
            if i == 0:  # 首条就挂 → 嵌入不可用，整体退化
                return None
            failed.append(i)
        vecs.append(v)
        if (i + 1) % 50 == 0:
            print(f"  …已嵌入 {i + 1}/{len(records)}", file=sys.stderr)
    if failed:
        dim = len(next(v for v in vecs if v is not None))
        good = [v for v in vecs if v is not None]
        centroid = _unit([sum(v[d] for v in good) / len(good) for d in range(dim)])
        for i in failed:
            vecs[i] = centroid
        ids = [records[i]["id"] for i in failed][:5]
        print(f"警告：{len(failed)} 条嵌入失败，已用质心占位：{ids}", file=sys.stderr)
    return vecs


def _cos_dist(u, v):
    return 1.0 - sum(a * b for a, b in zip(u, v))


def _axis_key(r):
    """正交轴：MedBench 的「能力」=task；book 的「专科」=domain。
    这是两路各自**设计上的正交维度**——多样性应优先铺开它们，而非在同一能力里反复取题。"""
    return ("mb", r["task"]) if r["track"] == "medbench" else ("bk", r.get("domain"))


def coverage_seeds(records, diff):
    """每个 MedBench 任务取最难的一条 → 保证 12 项 Agent 能力在 mini 里全部到场。

    book 有 37 专科 > 30，mini 装不下，只能靠 MMR 新颖度铺开；但 MedBench 12 能力 ≤ 30，
    是**可保证全覆盖**的，且这 12 项正是项目的头号正交轴，故对它做硬覆盖、其余交给 MMR。
    """
    best = {}
    for i, r in enumerate(records):
        if r["track"] != "medbench":
            continue
        t = r["task"]
        if t not in best or diff[i] > diff[best[t]]:
            best[t] = i
    return sorted(best.values(), key=lambda i: (-diff[i], i))


def mmr_rank(records, diff, vecs, lam, preselected=None):
    """MMR 贪心全量排名（确定性，稳定 argmax）。每步选 argmax：

        λ·难度 + (1-λ)·[ 0.5·语义距离(到已选最小余弦距) + 0.5·结构新颖度 ]

    结构新颖度 = 1/(1+该正交轴已选数)：新能力/新专科得 1.0，重复则衰减——
    防止「最难」把整张卷灌成同一个任务（如 MedDefend），保证按能力/专科铺开。
    `preselected`（如 coverage_seeds）按序置顶，其余贪心填充。
    """
    n = len(records)
    preselected = preselected or [max(range(n), key=lambda i: (diff[i], -i))]
    order = list(preselected)
    selected = set(order)
    min_dist = [min(_cos_dist(vecs[s], vecs[j]) for s in order) for j in range(n)]
    axis_count = {}
    for s in order:
        ak = _axis_key(records[s])
        axis_count[ak] = axis_count.get(ak, 0) + 1
    while len(order) < n:
        best, best_s = None, None
        for i in range(n):
            if i in selected:
                continue
            novelty = 1.0 / (1 + axis_count.get(_axis_key(records[i]), 0))
            div = 0.5 * min_dist[i] + 0.5 * novelty
            s = lam * diff[i] + (1 - lam) * div
            if best_s is None or s > best_s:
                best, best_s = i, s
        order.append(best)
        selected.add(best)
        ak = _axis_key(records[best])
        axis_count[ak] = axis_count.get(ak, 0) + 1
        for j in range(n):  # 增量更新到已选集的最小距离
            d = _cos_dist(vecs[best], vecs[j])
            if d < min_dist[j]:
                min_dist[j] = d
    return order


def structural_rank(records, diff, preselected=None):
    """无嵌入退化：按 (track,task,domain) 分桶 → 桶内按难度降序 → 桶间按「桶内最高难度」
    降序轮询。难度领跑保证最难的 MedDefend/MedShield 桶与最难的专科桶都早早入选、两路均衡，
    不会像按桶名排序那样把 book 桶全排到 medbench 前面。`preselected` 置顶（已去重）。"""
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
    # 桶间顺序：按桶内最高难度降序（平手按桶名稳定），轮询时强者优先
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
        f"  lambda: {meta['lambda']}",
        f"  embed_model: {meta['embed_model']}",
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
    ap = argparse.ArgumentParser(description="生成分层 mini-bench 子集")
    ap.add_argument("--lambda", dest="lam", type=float, default=0.5,
                    help="MMR 权衡：越大越偏难，越小越偏正交（默认 0.5）")
    ap.add_argument("--no-embed", action="store_true", help="不用句向量，退化为结构化分桶")
    ap.add_argument("--no-cover", action="store_true",
                    help="关掉「保证 12 项 MedBench 能力全覆盖」的硬置顶（纯按难度+正交挑）")
    ap.add_argument("--embed-model", default="nomic-embed-text")
    args = ap.parse_args()

    records = load_medbench() + load_book()
    n_a = sum(1 for r in records if r["gold_type"] == "reference")
    n_b = len(records) - n_a
    print(f"载入 {len(records)} 条（medbench {n_a} + book {n_b}）。计算难度…", file=sys.stderr)
    diff = compute_difficulty(records)

    method = "structural"
    vecs = None
    if not args.no_embed:
        print("求句向量（nomic-embed-text，首次较慢，之后命中缓存）…", file=sys.stderr)
        os.environ["EMBED_MODEL"] = args.embed_model
        vecs = embed_records(records)
        if vecs is None:
            print("→ 嵌入不可用，退化为结构化分桶选择。", file=sys.stderr)

    seeds = [] if args.no_cover else coverage_seeds(records, diff)
    if seeds:
        print(f"硬覆盖 {len(seeds)} 项 MedBench 能力（每任务取最难一条）置顶。", file=sys.stderr)
    if vecs is not None:
        method = "embed-mmr"
        order = mmr_rank(records, diff, vecs, args.lam, preselected=seeds or None)
    else:
        order = structural_rank(records, diff, preselected=seeds)

    os.makedirs(SUBSET_DIR, exist_ok=True)
    meta = {"method": method, "lambda": args.lam, "embed_model": args.embed_model,
            "generated": str(date.today()), "n_a": n_a, "n_b": n_b}

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
