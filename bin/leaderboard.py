#!/usr/bin/env python3
"""leaderboard.py — 跨模型排行榜聚合器（cross-model leaderboard）。零外部依赖、纯确定性。

读 eval/results/*.json（每个=单模型单次跑：{summary,results}），把 summary.model 贴到每条
results 行上，跨所有跑聚合，产出按**三个互不可比的度量族**分轴的排行榜：
  1. capability   Track A（gold_type=reference）  每 task → 0–40 分
  2. specialty    Track B（gold_type=criteria）   每 domain → 0–40 分 + unsupported 率
  3. orchestration/robustness  family-3（Accuracy %）：
       probe（gold_type=probe，success 布尔）、routing（track=routing）、
       tool_decision（track=tool_decision，TIA）；live（track=live）= 抗污染 capability

设计要点（与计划一致）：
  - 去重：按 (model,track,task,id) 取**最新一跑**（文件名时间戳字典序=时序），重跑覆盖不重复计。
  - bootstrap 95% CI：每 (model,bucket) 出 ci_low/ci_high；mini 桶 n 小、点排名是噪声，
    CI 供 B 判并列。
  - judge 偏置诊断：长度↔分相关（从 model_response 现算）；judge 同源 self-preference 标记。
  - 抗污染：标记每模型是否有 family-3/live（抗 MedBench 记忆）数据，供 routing 降权 Track A。
  - 不可比铁律：三族分轴呈现，A/B 各自 0–40 但规约不同，**永不合并成一个顶线**。

不可变性：只读结果文件，逐行 emit 新对象，从不就地修改。
用法：
  python3 bin/leaderboard.py                 # 写 eval/leaderboard.json
  python3 bin/leaderboard.py --md            # 同时打印人类可读表（也写 eval/leaderboard.md）
  python3 bin/leaderboard.py --common        # 每桶仅取所有受比模型共有的 record-id（严格可比）
"""
import argparse
import glob
import json
import math
import os
import random
import re
import sys
from collections import defaultdict
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from specialty_map import broad_area  # noqa: E402

ROOT_DIR = os.path.dirname(SCRIPT_DIR)
RESULTS_DIR = os.path.join(ROOT_DIR, "eval", "results")
OUT_JSON = os.path.join(ROOT_DIR, "eval", "leaderboard.json")
OUT_MD = os.path.join(ROOT_DIR, "eval", "leaderboard.md")

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
BOOT_N = 1000          # bootstrap 重采样次数
BOOT_SEED = 20260610   # 固定种子 → CI 可复现


def _classify(row):
    """把一条结果行映射到 (family, bucket, value, is_accuracy)；无法分类返回 None。

    value：capability/specialty/live = 0–40 综合分；orchestration/robustness = 1.0/0.0。
    """
    if row.get("error"):
        return None
    track = row.get("track")
    gtype = row.get("gold_type")

    if gtype == "probe":
        bucket = row.get("probe_kind") or row.get("task") or "probe"
        return ("robustness", str(bucket), 1.0 if row.get("success") else 0.0, True)
    if track == "routing":
        return ("orchestration", "specialty_routing", 1.0 if row.get("correct") else 0.0, True)
    if track == "tool_decision":
        return ("orchestration", "tool_invocation_awareness",
                1.0 if row.get("correct") else 0.0, True)

    scores = row.get("scores") or {}
    total = scores.get("total")
    if total is None:
        return None
    if track == "live":
        return ("live", str(row.get("domain") or "live"), float(total), False)
    if gtype == "reference":
        return ("capability", str(row.get("task") or "?"), float(total), False)
    if gtype == "criteria":
        return ("specialty", str(row.get("domain") or "?"), float(total), False)
    return None


def load_rows():
    """读所有结果文件，按时间戳升序，(model,track,task,id) 去重取最新。返回 model→记录列表。"""
    files = sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json")))
    # key=(model,track,task,id) → (ts, enriched_row)；后读（更晚 ts）覆盖
    latest = {}
    for path in files:
        m = TS_RE.search(os.path.basename(path))
        ts = m.group(1) if m else os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        summary = blob.get("summary") or {}
        model = summary.get("model")
        judge = summary.get("judge_model")
        if not model:
            continue
        for row in blob.get("results") or []:
            key = (model, row.get("track"), row.get("task"), row.get("id"))
            prev = latest.get(key)
            if prev is None or ts >= prev[0]:
                enriched = dict(row)
                enriched["_model"] = model
                enriched["_judge_model"] = judge
                latest[key] = (ts, enriched)
    by_model = defaultdict(list)
    for _key, (_ts, row) in latest.items():
        by_model[row["_model"]].append(row)
    return by_model


def _bootstrap_ci(values):
    """确定性 bootstrap 95% CI（均值）。n<2 → CI=点值（无离散度可估）。"""
    n = len(values)
    if n == 0:
        return (None, None)
    mean = sum(values) / n
    if n < 2:
        return (round(mean, 2), round(mean, 2))
    rng = random.Random(BOOT_SEED)
    means = []
    for _ in range(BOOT_N):
        s = sum(values[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * BOOT_N)]
    hi = means[int(0.975 * BOOT_N)]
    return (round(lo, 2), round(hi, 2))


def _pearson(xs, ys):
    """长度↔分相关。<3 点或零方差 → None。"""
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return round(sxy / math.sqrt(sxx * syy), 3)


def _cell(values, is_accuracy):
    """一个 (model,bucket) 单元：n、均值（accuracy→0–1，否则 0–40）、bootstrap CI。"""
    n = len(values)
    mean = sum(values) / n if n else 0.0
    lo, hi = _bootstrap_ci(values)
    cell = {"n": n, "ci_low": lo, "ci_high": hi}
    if is_accuracy:
        cell["accuracy"] = round(mean, 3)
    else:
        cell["avg_total"] = round(mean, 1)
    return cell


def aggregate(by_model, common=False):
    """聚合成 {family: {bucket: {model: cell}}} + 每模型诊断。"""
    # family → bucket → model → [(record_id, value, row)]
    fam = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    diagnostic_pts = defaultdict(lambda: {"len": [], "score": []})
    judge_models = defaultdict(set)
    resistant = defaultdict(bool)

    for model, rows in by_model.items():
        for row in rows:
            cl = _classify(row)
            if cl is None:
                continue
            family, bucket, value, is_acc = cl
            rid = row.get("id")
            fam[family][bucket][model].append((rid, value, row))
            if row.get("_judge_model"):
                judge_models[model].add(row["_judge_model"])
            if family in ("robustness", "orchestration", "live"):
                resistant[model] = True
            # 长度↔分诊断仅用 0–40 评分行（capability/specialty/live）
            if not is_acc:
                resp = row.get("model_response") or ""
                diagnostic_pts[model]["len"].append(len(resp))
                diagnostic_pts[model]["score"].append(value)

    out = {}
    for family, buckets in fam.items():
        out[family] = {}
        for bucket, models in buckets.items():
            if common and len(models) > 1:
                # 仅保留所有受比模型共有的 record-id（严格 apples-to-apples）
                idsets = [set(rid for rid, _v, _r in pts) for pts in models.values()]
                shared = set.intersection(*idsets) if idsets else set()
                models = {
                    m: [(rid, v, r) for rid, v, r in pts if rid in shared]
                    for m, pts in models.items()
                }
            cells = {}
            for model, pts in models.items():
                vals = [v for _rid, v, _r in pts]
                if not vals:
                    continue
                is_acc = family in ("robustness", "orchestration")
                cell = _cell(vals, is_acc)
                if family == "specialty":
                    rows_ = [r for _rid, _v, r in pts]
                    # 优先：claim 级 unsupported_rate（FActScore/HealthBench-Hallu，--hallu 时有）
                    # = Σunsupported / Σclaims（语料级，比逐答 grounding_source 二元更细可引用）
                    hrows = [r for r in rows_ if r.get("hallu")]
                    if hrows:
                        tot_claims = sum(r["hallu"]["n_claims"] for r in hrows)
                        tot_unsup = sum(r["hallu"]["unsupported"] for r in hrows)
                        tot_sup = sum(r["hallu"]["supported"] for r in hrows)
                        denom = tot_sup + tot_unsup
                        if tot_claims:
                            cell["unsupported_rate"] = round(tot_unsup / tot_claims, 3)
                            cell["unsupported_metric"] = "claim"   # FActScore 式
                            cell["factual_precision"] = round(tot_sup / denom, 3) if denom else 1.0
                    # 回退：逐答 grounding_source 二元（无 --hallu 时的过渡口径）
                    if "unsupported_rate" not in cell:
                        gs = [r.get("grounding_source") for r in rows_
                              if r.get("grounding_source")]
                        if gs:
                            cell["unsupported_rate"] = round(
                                sum(1 for g in gs if g == "unsupported") / len(gs), 3)
                            cell["unsupported_metric"] = "response"
                    sfv = sum(1 for r in rows_ if r.get("hallucinated"))
                    cell["safety_floor_violation_rate"] = round(sfv / len(rows_), 3)
                elif family == "capability":
                    rows_ = [r for _rid, _v, r in pts]
                    passes = sum(1 for r in rows_ if r.get("pass"))
                    cell["pass_rate"] = round(passes / len(rows_), 3)
                cells[model] = cell
            if cells:
                out[family][bucket] = cells

    # 专科汇总（specialty_rollup）：把 specialty 的逐 domain 行按 broad_area（内科/精神科）
    # 重新归并成稳定大桶。多数 domain n<5（见 specialty_report），逐 domain 分是噪声；
    # 此 rollup 给「模型在内科 vs 精神科孰强孰弱」一个有足够 n 的一等读数。
    spec = fam.get("specialty") or {}
    rollup = defaultdict(lambda: defaultdict(list))  # area → model → [(rid,val,row)]
    for domain, models in spec.items():
        for model, pts in models.items():
            for rid, val, row in pts:
                area = broad_area(row.get("domain"), row.get("gold_source"))
                rollup[area][model].append((rid, val, row))
    out["specialty_rollup"] = {}
    for area, models in rollup.items():
        cells = {}
        for model, pts in models.items():
            vals = [v for _rid, v, _r in pts]
            if not vals:
                continue
            cell = _cell(vals, is_accuracy=False)
            rows_ = [r for _rid, _v, r in pts]
            hrows = [r for r in rows_ if r.get("hallu")]
            if hrows:
                tot_claims = sum(r["hallu"]["n_claims"] for r in hrows)
                tot_unsup = sum(r["hallu"]["unsupported"] for r in hrows)
                if tot_claims:
                    cell["unsupported_rate"] = round(tot_unsup / tot_claims, 3)
                    cell["unsupported_metric"] = "claim"
            cells[model] = cell
        if cells:
            out["specialty_rollup"][area] = cells

    diagnostics = {}
    for model in by_model:
        pts = diagnostic_pts[model]
        # HealthBench context-awareness：appropriate 率（情境觉察得当占比，可靠性信号）
        ctx = [r.get("context_awareness") for r in by_model[model]
               if r.get("context_awareness") in ("appropriate", "overconfident", "overhedged")]
        ctx_appropriate_rate = (
            round(sum(1 for c in ctx if c == "appropriate") / len(ctx), 3) if ctx else None)
        diagnostics[model] = {
            "n_scored": len(pts["score"]),
            "length_score_corr": _pearson(pts["len"], pts["score"]),
            "judge_models": sorted(judge_models[model]),
            "judge_family_conflict": _family_conflict(model, judge_models[model]),
            "has_contamination_resistant": resistant[model],
            "ctx_appropriate_rate": ctx_appropriate_rate,
            "ctx_n": len(ctx),
        }
    return out, diagnostics


def _family_conflict(model, judges):
    """候选与 judge 同源（self-preference 风险）：粗启发——共享族名 token。"""
    m = model.lower()
    for j in judges:
        jl = j.lower()
        for fam in ("deepseek", "qwen", "glm", "llama", "baichuan", "gpt", "minicpm"):
            if fam in m and fam in jl:
                return True
    return False


def render_md(agg, diagnostics):
    """三族分轴的人类可读表。"""
    lines = [f"# Leaderboard — {date.today().isoformat()}", ""]
    lines.append("> 三个度量族互不可比（capability/specialty 为 0–40 但规约不同；orchestration 为 "
                 "Accuracy）。**勿跨族比较**。CI 重叠的模型视为并列（见 routing_manifest）。")
    lines.append("")
    fam_titles = [
        ("capability", "Capability — Track A（每 task，0–40，⚠ 公开榜数据有记忆/污染风险）"),
        ("live", "Live — 抗污染 capability（兄弟现答为参考，每 domain，0–40）"),
        ("specialty", "Specialty — Track B（每 domain，0–40 + unsupported 率）"),
        ("specialty_rollup", "Specialty 汇总 — 按科室大类（内科/精神科，n 充足的稳定读数）"),
        ("robustness", "Robustness — 探针（false_premise/nonexistent，Accuracy）"),
        ("orchestration", "Orchestration — 编排（routing / TIA，Accuracy）"),
    ]
    for family, title in fam_titles:
        buckets = agg.get(family) or {}
        if not buckets:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for bucket in sorted(buckets):
            cells = buckets[bucket]
            lines.append(f"### {bucket}")
            ranked = sorted(
                cells.items(),
                key=lambda kv: kv[1].get("accuracy", kv[1].get("avg_total", 0)),
                reverse=True,
            )
            for model, c in ranked:
                if "accuracy" in c:
                    val = f"acc {c['accuracy']:.2f}"
                else:
                    val = f"{c['avg_total']:.1f}/40"
                ci = ""
                if c.get("ci_low") is not None:
                    ci = f"  CI[{c['ci_low']}, {c['ci_high']}]"
                extra = ""
                if "unsupported_rate" in c:
                    tag = "·claim" if c.get("unsupported_metric") == "claim" else ""
                    extra += f"  unsupported{tag} {c['unsupported_rate']:.2f}"
                    if "factual_precision" in c:
                        extra += f"  factprec {c['factual_precision']:.2f}"
                if "safety_floor_violation_rate" in c:
                    extra += f"  safety⚑ {c['safety_floor_violation_rate']:.2f}"
                if "pass_rate" in c:
                    extra += f"  pass {c['pass_rate']:.2f}"
                lines.append(f"- {model:<28} n={c['n']:<3} {val}{ci}{extra}")
            lines.append("")
    lines.append("## Diagnostics（judge 有效性）")
    lines.append("")
    for model in sorted(diagnostics):
        d = diagnostics[model]
        corr = d["length_score_corr"]
        warn = " ⚠长度偏置" if corr is not None and abs(corr) >= 0.5 else ""
        conflict = " ⚠self-preference" if d["judge_family_conflict"] else ""
        resist = "" if d["has_contamination_resistant"] else " ⚠仅静态榜数据"
        ctx = ""
        if d.get("ctx_appropriate_rate") is not None:
            ctx = f"  ctx-appropriate {d['ctx_appropriate_rate']:.2f}(n={d['ctx_n']})"
        lines.append(f"- {model:<26} n={d['n_scored']:<3} r={corr}{warn}{conflict}{resist}{ctx}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="跨模型排行榜聚合")
    ap.add_argument("--md", action="store_true", help="同时打印并写 leaderboard.md")
    ap.add_argument("--common", action="store_true",
                    help="每桶仅取所有受比模型共有的 record-id（严格可比）")
    args = ap.parse_args()

    by_model = load_rows()
    agg, diagnostics = aggregate(by_model, common=args.common)

    out = {
        "generated": date.today().isoformat(),
        "common_subset": args.common,
        "models": sorted(by_model),
        "families": agg,
        "diagnostics": diagnostics,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    if args.md:
        md = render_md(agg, diagnostics)
        with open(OUT_MD, "w", encoding="utf-8") as f:
            f.write(md)
        print(md)
    print(f"\n→ {os.path.relpath(OUT_JSON, ROOT_DIR)}  "
          f"({len(by_model)} 模型, {sum(len(v) for v in by_model.values())} 去重记录)")


if __name__ == "__main__":
    main()
