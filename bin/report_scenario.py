#!/usr/bin/env python3
"""report_scenario.py — 阶段2 收尾：理论短名单 + 实测排行榜 → 单场景报告(Markdown)。

读 eval/theory/shortlist.yaml(阶段1先验) + eval/leaderboard.json(阶段2实测聚合)，
产出 eval/reports/<domain>-<axis>-<date>.md：
  场景定义 → 理论短名单(引 agent-bench) → 天花板参照 → 实测三轴分列
  (能力 Track A 0–40 / 诚信 Track B 0–40+unsupported / 编排稳健 族③ Accuracy)
  → 理论 vs 实测差异点 → 本场景结论(**仅就本场景，不产出通用 MoA 清单**)。

设计：纯读、纯拼装(不可变)；leaderboard.json 缺某族 → 该栏标 n/a，不报错。
名字对齐：shortlist 候选名 == model_pool 名 == leaderboard.json 各 bucket 的 model key。

用法：
  python3 bin/report_scenario.py \\
      --shortlist eval/theory/shortlist.yaml \\
      --leaderboard eval/leaderboard.json --out eval/reports/
"""
import argparse
import json
import os
from datetime import date

import yaml

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve(p):
    """相对路径按 ROOT_DIR 解析；绝对路径原样返回。"""
    return p if os.path.isabs(p) else os.path.join(ROOT_DIR, p)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def combined_score(r):
    """本地候选的实测综合（能力 + 诚信，各 0–40）；无数据 → 0。"""
    return mean([r["capability"], r["honesty"]]) or 0.0


def axis_capability(families, model):
    """Track A：capability 各 task 桶 avg_total 的均值(0–40)。"""
    buckets = families.get("capability") or {}
    return mean([b.get(model, {}).get("avg_total") for b in buckets.values()])


def axis_honesty(families, model):
    """Track B：specialty 各 domain 桶 avg_total 均值 + unsupported_rate 均值。"""
    buckets = families.get("specialty") or {}
    score = mean([b.get(model, {}).get("avg_total") for b in buckets.values()])
    unsup = mean([b.get(model, {}).get("unsupported_rate") for b in buckets.values()])
    return score, unsup


def axis_orchestration(families, model):
    """族③：orchestration + robustness 各桶 accuracy 均值(0–1)。"""
    accs = []
    for fam in ("orchestration", "robustness"):
        for b in (families.get(fam) or {}).values():
            accs.append(b.get(model, {}).get("accuracy"))
    return mean(accs)


def measured_rows(candidates, families):
    rows = []
    for c in candidates:
        m = c["model"]
        honesty, unsup = axis_honesty(families, m)
        rows.append({
            "model": m, "theory": c["theory_score"], "tier": c["tier"],
            "capability": axis_capability(families, m),
            "honesty": honesty, "unsupported": unsup,
            "orchestration": axis_orchestration(families, m),
        })
    return rows


def fmt(v, pct=False):
    if v is None:
        return "n/a"
    return f"{v:.0%}" if pct else f"{v}"


def diff_notes(rows):
    """理论 vs 实测的差异点(纸面高/实测崩 之类)。无实测数据的不评。"""
    out = []
    measured = [r for r in rows if r["capability"] is not None or r["honesty"] is not None]
    if len(measured) < 2:
        return ["实测候选 < 2，暂不评理论↔实测一致性（先把短名单跑出实测数据）。"]
    # 实测综合（能力+诚信，各 0–40）排序 vs 理论排序
    by_measured = sorted(measured, key=combined_score, reverse=True)
    by_theory = sorted(measured, key=lambda r: r["theory"], reverse=True)
    if [r["model"] for r in by_measured] != [r["model"] for r in by_theory]:
        out.append(f"理论序 {[r['model'] for r in by_theory]} ≠ 实测序 "
                   f"{[r['model'] for r in by_measured]} —— 纸面先验未必兑现，以实测为准。")
    else:
        out.append("理论序与实测序一致（本场景先验与实测同向）。")
    for r in measured:                     # 诚信崩 / 编排崩 单点告警
        if r["unsupported"] is not None and r["unsupported"] >= 0.2:
            out.append(f"⚠ {r['model']} 诚信轴 unsupported_rate={r['unsupported']:.0%}"
                       f"（幻觉偏高，面向患者慎用）。")
        if r["orchestration"] is not None and r["orchestration"] < 0.5:
            out.append(f"⚠ {r['model']} 编排稳健 accuracy={r['orchestration']:.0%}"
                       f"（选科/拒答能力弱）。")
    return out


def render(scenario, rows, ceilings, notes):
    d = scenario
    L = [f"# 场景报告 · {d['domain']} × {d['axis']}（{date.today().isoformat()}）", "",
         "> 阶段1理论先验(agent-bench) + 阶段2按场景实测。**三轴互不可比**；",
         "> 结论**仅就本场景**，不产出通用 MoA 路由清单。", "",
         f"**场景定义**：domain=`{d['domain']}` · axis=`{d['axis']}` · "
         f"理论评定 as_of `{d.get('as_of', '?')}`。", "",
         "## 理论短名单（先验，引 agent-bench）", "",
         "| 模型 | tier | theory_score | 依据 |", "|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['model']} | {r['tier']} | {r['theory']} | 见 shortlist.yaml.why |")
    L += ["", "### 天花板参照（闭源，testable=false，仅作上界）", ""]
    if ceilings:
        L += ["| 标杆 | theory_score | 出处 |", "|---|---|---|"]
        L += [f"| {c['model']} | {c['theory_score']} | "
              f"{(c.get('source_entries') or ['?'])[0]} |" for c in ceilings]
    else:
        L.append("（无）")
    L += ["", "## 实测三轴（按场景执行后）", "",
          "| 模型 | 能力 Track A (0–40) | 诚信 Track B (0–40) | unsupported | 编排稳健 ③ (Acc) |",
          "|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['model']} | {fmt(r['capability'])} | {fmt(r['honesty'])} | "
                 f"{fmt(r['unsupported'], pct=True)} | {fmt(r['orchestration'], pct=True)} |")
    L += ["", "## 理论 vs 实测 差异点", ""]
    L += [f"- {n}" for n in notes]
    L += ["", "## 本场景结论", "",
          _conclusion(rows), ""]
    return "\n".join(L) + "\n"


def _conclusion(rows):
    measured = [r for r in rows if r["capability"] is not None or r["honesty"] is not None]
    if not measured:
        return ("短名单尚无实测数据——先 `./bin/run_pool.sh --from-shortlist … && "
                "./bin/leaderboard.sh` 再回看本报告。")
    best = max(measured, key=combined_score)
    return (f"本场景实测综合最优：**{best['model']}**"
            f"（能力 {fmt(best['capability'])} / 诚信 {fmt(best['honesty'])} / "
            f"编排 {fmt(best['orchestration'], pct=True)}）。"
            "仅就此场景推荐，换场景需重跑阶段1+2。")


def main():
    ap = argparse.ArgumentParser(description="阶段2 单场景报告")
    ap.add_argument("--shortlist", default="eval/theory/shortlist.yaml")
    ap.add_argument("--leaderboard", default="eval/leaderboard.json")
    ap.add_argument("--out", default="eval/reports/", help="输出目录")
    args = ap.parse_args()

    sl_path = _resolve(args.shortlist)
    lb_path = _resolve(args.leaderboard)
    if not os.path.isfile(sl_path):
        raise SystemExit(f"错误：短名单不存在：{sl_path}（先跑 theory_screen.py）")

    with open(sl_path, encoding="utf-8") as f:
        sl = yaml.safe_load(f) or {}
    scenario = sl.get("scenario") or {"domain": "?", "axis": "?"}
    candidates = [c for c in (sl.get("candidates") or []) if c.get("testable")]
    ceilings = sl.get("ceiling_refs") or []

    families = {}
    if os.path.isfile(lb_path):
        with open(lb_path, encoding="utf-8") as f:
            families = (json.load(f) or {}).get("families") or {}
    else:
        print(f"提示：无 {lb_path}，实测三轴将全 n/a（先跑 leaderboard.sh）。")

    rows = measured_rows(candidates, families)
    notes = diff_notes(rows)
    md = render(scenario, rows, ceilings, notes)

    out_dir = _resolve(args.out)
    os.makedirs(out_dir, exist_ok=True)
    name = f"{scenario['domain']}-{scenario['axis']}-{date.today().isoformat()}.md"
    out_path = os.path.join(out_dir, name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"✓ 写场景报告 {out_path}")


if __name__ == "__main__":
    main()
