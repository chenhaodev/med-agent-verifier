#!/usr/bin/env python3
"""build_routing.py — 由排行榜派生**带排名的路由清单** eval/routing_manifest.yaml。

消费 eval/leaderboard.json（leaderboard.py 产出），为每个桶（capability 的 task / specialty 的
domain）给出 **top-k** 候选模型；并由 family-3 指标产出 `orchestrator:` 主模型排名。这是**之后**
离线 MoA 系统唯一读取的契约文件——本脚本不构建 MoA。

关键规则（与计划一致）：
  - 资格门槛：仅纳入 n>=min_n（默认 5）且指标>=floor 的模型；桶太薄 → insufficient_data。
  - CI 重叠并列：bootstrap CI（来自 A）重叠的模型不强排 1-2-3，置为 top 并列簇 → 天然喂给
    route-to-top-k + aggregate 的 MoA。mini 派生的桶内排名仅作 triage，可靠路由需 medium/large。
  - 抗污染降权：Track A（capability）为公开榜数据，标 contamination_risk；orchestrator 合成分对
    抗污染信号（routing/TIA/specialty）给更高权重。
  - orchestrator 主模型：由 specialty_routing + TIA + 编排相邻 Track A 任务
    （MedDecomp/MedPathPlan/MedReflect/MedCollab）合成排名。

用法：
  python3 bin/build_routing.py                       # 默认 k=3, min_n=5
  python3 bin/build_routing.py --top-k 2 --min-n 3 --floor 20
"""
import argparse
import json
import os
from datetime import date

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
LB_JSON = os.path.join(ROOT_DIR, "eval", "leaderboard.json")
OUT_YAML = os.path.join(ROOT_DIR, "eval", "routing_manifest.yaml")

# 编排相邻能力：MoA 主模型需要的拆解/规划/反思/协作能力（Track A 任务名）
ORCH_TASKS = ["MedDecomp", "MedPathPlan", "MedReflect", "MedCollab"]
ACC_FAMILIES = ("robustness", "orchestration")

HEADER = """\
# routing_manifest.yaml — 由 build_routing.py 自 eval/leaderboard.json 派生（勿手改）。
#
# 契约：这是**之后**离线 MoA（route-to-top-k + aggregate，推理期零 API）唯一读取的文件。
#   - domains/tasks 下每桶给 ranked（top-k）+ tied_top（CI 重叠并列簇，MoA 取整簇做聚合）。
#   - insufficient_data=true 的桶数据太薄，MoA 应回退到 default 模型。
#   - capability(Track A) 带 contamination_risk：公开榜数据可能被记忆，应低于抗污染信号取信。
#   - orchestrator: 由编排指标合成，供 MoA 选主/聚合模型。
# 三个度量族互不可比，分节呈现，勿跨节比较。
"""


def _metric(cell):
    """单元的主指标：accuracy（0–1）优先，否则 avg_total（0–40）。"""
    if "accuracy" in cell:
        return cell["accuracy"]
    return cell.get("avg_total", 0.0)


def _ci(cell):
    lo, hi = cell.get("ci_low"), cell.get("ci_high")
    if lo is None or hi is None:
        m = _metric(cell)
        return (m, m)
    return (lo, hi)


def _overlap(a, b):
    """两 CI 区间是否重叠。"""
    return a[0] <= b[1] and b[0] <= a[1]


def rank_bucket(cells, top_k, min_n, floor, contamination_risk=False):
    """对一个桶的 {model: cell} 排名；返回带 ranked/tied_top/insufficient_data 的 dict。"""
    eligible = {
        m: c for m, c in cells.items()
        if c.get("n", 0) >= min_n and _metric(c) >= floor
    }
    node = {}
    if contamination_risk:
        node["contamination_risk"] = "unverified"
    if not eligible:
        node["insufficient_data"] = True
        # 仍列出已有候选（带 n），供人工判断 / MoA 回退参考
        node["candidates"] = [
            {"model": m, **_score_fields(c)}
            for m, c in sorted(cells.items(), key=lambda kv: _metric(kv[1]), reverse=True)
        ]
        return node

    ranked = sorted(eligible.items(), key=lambda kv: _metric(kv[1]), reverse=True)
    top_ci = _ci(ranked[0][1])
    tied = [m for m, c in ranked if _overlap(_ci(c), top_ci)]
    node["tied_top"] = tied
    node["ranked"] = [
        {"model": m, **_score_fields(c)} for m, c in ranked[:top_k]
    ]
    return node


def _score_fields(cell):
    """清单里每个候选展示的字段。"""
    f = {"n": cell.get("n")}
    if "accuracy" in cell:
        f["accuracy"] = cell["accuracy"]
    else:
        f["avg_total"] = cell.get("avg_total")
    if cell.get("ci_low") is not None:
        f["ci"] = [cell["ci_low"], cell["ci_high"]]
    return f


def build_orchestrator(families, top_k, min_n):
    """合成主模型排名：抗污染编排信号(routing/TIA)权重高，编排相邻 Track A 任务权重低。"""
    # 收集每模型的各信号
    comp = {}  # model -> {"parts": {...}, "score": float, "weight": float}
    orch = families.get("orchestration", {})
    cap = families.get("capability", {})

    def add(model, value01, weight, label):
        d = comp.setdefault(model, {"parts": {}, "wsum": 0.0, "w": 0.0})
        d["parts"][label] = round(value01, 3)
        d["wsum"] += value01 * weight
        d["w"] += weight

    # 抗污染编排信号（权重 1.0）
    for bucket, cells in orch.items():
        for model, c in cells.items():
            if c.get("n", 0) >= min_n:
                add(model, c.get("accuracy", 0.0), 1.0, bucket)
    # 编排相邻 Track A 任务（污染风险 → 权重 0.5；0–40 归一到 0–1）
    for task in ORCH_TASKS:
        cells = cap.get(task, {})
        for model, c in cells.items():
            if c.get("n", 0) >= min_n:
                add(model, c.get("avg_total", 0.0) / 40.0, 0.5, f"cap:{task}")

    if not comp:
        return {"insufficient_data": True,
                "note": "无 routing/TIA/编排能力数据；先跑 eval_routing.py 与 --track probe。"}

    ranked = []
    for model, d in comp.items():
        score = round(d["wsum"] / d["w"], 3) if d["w"] else 0.0
        ranked.append((score, model, d["parts"]))
    ranked.sort(reverse=True)
    return {
        "ranked": [
            {"model": m, "composite": s, "signals": parts}
            for s, m, parts in ranked[:top_k]
        ],
        "weighting": "抗污染编排信号(routing/TIA)=1.0；Track A 编排相邻任务=0.5（污染降权）",
    }


def main():
    ap = argparse.ArgumentParser(description="由排行榜派生路由清单")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--min-n", type=int, default=5)
    ap.add_argument("--floor", type=float, default=0.0, help="指标下限（0–40 或 0–1，按族）")
    args = ap.parse_args()

    if not os.path.exists(LB_JSON):
        raise SystemExit("缺少 eval/leaderboard.json，请先跑 bin/leaderboard.py。")
    with open(LB_JSON, encoding="utf-8") as f:
        lb = json.load(f)
    families = lb.get("families", {})

    manifest = {
        "generated": date.today().isoformat(),
        "source": "eval/leaderboard.json",
        "params": {"top_k": args.top_k, "min_n": args.min_n, "floor": args.floor},
        "tasks": {},     # capability，Track A，污染风险
        "domains": {},   # specialty，Track B，抗污染
        "orchestrator": {},
    }

    for task, cells in (families.get("capability") or {}).items():
        manifest["tasks"][task] = rank_bucket(
            cells, args.top_k, args.min_n, args.floor, contamination_risk=True)
    for domain, cells in (families.get("specialty") or {}).items():
        manifest["domains"][domain] = rank_bucket(
            cells, args.top_k, args.min_n, args.floor)
    # live（抗污染 capability）若有，并入 domains 标注
    for domain, cells in (families.get("live") or {}).items():
        key = f"{domain}__live"
        manifest["domains"][key] = rank_bucket(cells, args.top_k, args.min_n, args.floor)

    manifest["orchestrator"] = build_orchestrator(families, args.top_k, args.min_n)

    with open(OUT_YAML, "w", encoding="utf-8") as f:
        f.write(HEADER)
        yaml.safe_dump(manifest, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    n_tasks = len(manifest["tasks"])
    n_dom = len(manifest["domains"])
    print(f"→ {os.path.relpath(OUT_YAML, ROOT_DIR)}  "
          f"({n_tasks} tasks, {n_dom} domains, orchestrator="
          f"{'ok' if manifest['orchestrator'].get('ranked') else 'insufficient'})")


if __name__ == "__main__":
    main()
