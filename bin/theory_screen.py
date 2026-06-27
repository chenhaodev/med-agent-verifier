#!/usr/bin/env python3
"""theory_screen.py — 阶段1 理论评定：读 agent-bench 证据 → 候选短名单（纯纸面、零 LLM）。

两阶段流程的第一阶段(见 docs/THEORY-VS-EXECUTE.md)：用 ../agent-bench/entries/*.md 的
frontmatter 证据，按「场景」(domain × axis)给每个候选合成一个**理论分 theory_score**，
产出 must-test / optional / skip 短名单，交阶段2(run_pool --from-shortlist)按场景实测。

设计要点：
  · 纯启发式加权，零 LLM 调用、完全可解释、可复现(同输入同输出)。公式与默认权重见 SCORE_WEIGHTS，
    并落 eval/METRICS.md，CLI 可覆盖。theory_score 是**先验/triage，不替代实测分**。
  · 闭源标杆 ≠ 可实测候选(关键接缝)：agent-bench 的 models_ranked 几乎全是闭源前沿模型
    (Claude/GPT/Gemini)。本脚本分两类输出——
      (a) 标杆 benchmark：闭源冠军，testable=false，作天花板参照；
      (b) 可实测候选：eval/model_pool.yaml 里的本地模型，testable=true。
    用**启发式同族**(模型名家族词 + 规模数字)把 (a) 的证据迁移到 (b) 的理论先验；无同族证据时
    退回中性先验(license/规模 tiebreak)，并在 why 里如实注明「无同族榜单证据」——绝不编造区分度。
  · 不可变：只读 entry/pool，逐条 emit 新 dict，从不就地修改。
  · frontmatter 解析法照搬 agent-bench/bin/render_site.py(yaml.safe_load 切 ^---\\n...\\n---)。

用法：
  python3 bin/theory_screen.py --domain medical --axis agent --top 5 \\
      --out eval/theory/shortlist.yaml
  python3 bin/theory_screen.py --domain medical --axis agent --prefer-license open-weights
"""
import argparse
import os
import re
import sys
from datetime import date

import yaml

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENTRIES = os.path.join(ROOT_DIR, "..", "agent-bench", "entries")
DEFAULT_POOL = os.path.join(ROOT_DIR, "eval", "model_pool.yaml")

# theory_score = w_rank·rank + w_auth·auth + w_verdict·verdict − p_contam·contam
SCORE_WEIGHTS = {"w_rank": 0.4, "w_auth": 0.3, "w_verdict": 0.3, "p_contam": 0.2}
VERDICT_SCORE = {"high": 1.0, "medium": 0.6, "low": 0.3}  # 缺 → CONSERVATIVE
CONSERVATIVE = 0.3
NEUTRAL_PRIOR = 0.4  # 无同族榜单证据时,可实测本地候选的中性先验
TIER_MUST = 0.55     # theory_score 阈值：≥ → must-test
TIER_OPT = 0.30      # ≥ → optional，否则 skip
FAMILY_WORDS = ("claude", "gpt", "gemini", "qwen", "llama", "glm", "baichuan",
                "mistral", "deepseek", "phi", "minimind", "safemed", "pulse")


# ── frontmatter 解析(照搬 agent-bench/bin/render_site.py 的切片法)──────────────
def parse_frontmatter(text):
    """切出 ^---\\n ... \\n--- 并 yaml.safe_load；无 frontmatter → {}。"""
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    return yaml.safe_load(m.group(1)) or {}


def load_entries(entries_dir):
    """读目录下所有 *.md(跳过 _TEMPLATE)→ [(stem, frontmatter_dict), ...]。"""
    out = []
    for fn in sorted(os.listdir(entries_dir)):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        with open(os.path.join(entries_dir, fn), encoding="utf-8") as f:
            fm = parse_frontmatter(f.read())
        if fm:
            out.append((fn[:-3], fm))
    return out


# ── 家族 / 规模归一(启发式同族匹配的基础)─────────────────────────────────────
def normalize_family(name):
    """模型名 → (family_word|None, size_in_billions|None)。纯字符串启发式，可解释。"""
    low = name.lower()
    family = next((w for w in FAMILY_WORDS if w in low), None)
    size = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", low)  # 32B / 7b / 1.5b / 0.8b
    if m:
        size = float(m.group(1))
    return family, size


def proximity(champ_size, cand_size):
    """同族下，规模接近度 → [0.4,1.0]。任一缺规模 → 0.7(中性)。"""
    if champ_size is None or cand_size is None:
        return 0.7
    ratio = min(champ_size, cand_size) / max(champ_size, cand_size)
    return round(0.4 + 0.6 * ratio, 3)


# ── 单 entry / 单 ranked-row 的各分量(全部回链真实字段)──────────────────────
def rank_score(rank):
    """rank1=1.0 递减；缺 rank → 0.5 保守。"""
    if not isinstance(rank, int) or rank < 1:
        return 0.5
    return max(0.0, 1.0 - 0.2 * (rank - 1))


def authority_score(authority):
    """由真实字段派生(无『临床参与』字段)：institution_count + citation + maintainers + cadence。

    无 authority 或字段缺失时按保守值代入(0)，绝不臆造。
    """
    a = authority or {}
    inst = min(1.0, (a.get("institution_count") or 0) / 500.0)
    cit = a.get("citation_count") or {}
    cit_v = min(1.0, (cit.get("value") or 0) / 1000.0)
    maint = min(1.0, len(a.get("maintainers") or []) / 3.0)
    cadence = {"live": 1.0, "monthly": 0.8, "quarterly": 0.6,
               "yearly": 0.4, "irregular": 0.4, "frozen": 0.2}.get(
        str(a.get("update_cadence", "")).strip(), 0.3)
    return round(0.4 * inst + 0.2 * cit_v + 0.2 * maint + 0.2 * cadence, 3)


def verdict_score(fm):
    conf = str((fm.get("expert_verdict") or {}).get("confidence", "")).strip()
    return VERDICT_SCORE.get(conf, CONSERVATIVE)


def contam_penalty(fm):
    """genre=online-leaderboard 且无抗污染说明 → 1.0(扣分)，否则 0。"""
    if str(fm.get("genre", "")).strip() != "online-leaderboard":
        return 0.0
    controls = (fm.get("methodology") or {}).get("contamination_controls")
    return 0.0 if (controls and str(controls).strip()) else 1.0


def entry_theory_score(fm, rank, w):
    """组合一个 (entry, ranked-row) 的 theory_score。"""
    s = (w["w_rank"] * rank_score(rank)
         + w["w_auth"] * authority_score(fm.get("authority"))
         + w["w_verdict"] * verdict_score(fm)
         - w["p_contam"] * contam_penalty(fm))
    return round(max(0.0, min(1.0, s)), 3)


def scenario_relevance(fm, row, domain, axis):
    """entry/row 与场景(domain×axis)的契合度 → 权重，用于把跨轴证据降权迁移。"""
    dom_hit = domain in [str(d).lower() for d in (fm.get("domain") or [])]
    axis_hit = str(row.get("axis", "")).strip().lower() == axis.lower()
    if dom_hit and axis_hit:
        return 1.0
    if axis_hit:
        return 0.7   # 同轴跨领域：先验可迁移，降权
    if dom_hit:
        return 0.5   # 同领域跨轴
    return 0.3


# ── 证据收集 + 候选合成 ────────────────────────────────────────────────────
def collect_champions(entries, domain, axis, w):
    """展开所有 entry 的 models_ranked → 标杆行(含场景契合度与 theory_score)。"""
    champs = []
    for stem, fm in entries:
        for row in fm.get("models_ranked") or []:
            name = str(row.get("model", "")).strip()
            if not name:
                continue
            fam, size = normalize_family(name)
            champs.append({
                "model": name, "entry": stem, "axis": str(row.get("axis", "")),
                "rank": row.get("rank"), "license": str(row.get("license", "unknown")),
                "family": fam, "size": size,
                "relevance": scenario_relevance(fm, row, domain, axis),
                "base_score": entry_theory_score(fm, row.get("rank"), w),
                "confidence": str((fm.get("expert_verdict") or {}).get("confidence", "n/a")),
            })
    return champs


def best_match(cand_name, champs):
    """本地候选 ← 同族标杆中证据最强者(relevance×proximity×base_score)。无同族 → None。"""
    fam, size = normalize_family(cand_name)
    if not fam:
        return None
    scored = []
    for c in champs:
        if c["family"] != fam:
            continue
        ev = c["relevance"] * proximity(c["size"], size) * c["base_score"]
        scored.append((round(ev, 3), c))
    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0]  # (evidence_score, champ_row)


def build_candidates(pool, champs, prefer_license):
    """每个本地可测模型 → 候选 dict(testable=true)。同族有证据用证据，否则中性先验。"""
    cands = []
    for e in pool:
        name = e["name"]
        fam, size = normalize_family(name)
        match = best_match(name, champs)
        if match:
            ev, c = match
            score = ev
            why = [
                f"启发式同族迁移：{c['model']}（{c['entry']}, axis={c['axis']}, "
                f"rank={c['rank']}）→ 本地 {name}（同族 {fam}，规模接近度计入）",
                f"标杆证据 base_score={c['base_score']}（authority+verdict+rank−contam），"
                f"场景契合度={c['relevance']}，专家信心={c['confidence']}",
            ]
            source = [c["entry"]]
        else:
            score = NEUTRAL_PRIOR
            why = [
                f"无同族榜单证据：agent-bench 该场景冠军无 {fam or '同族'} 本地近邻 → "
                f"中性先验 {NEUTRAL_PRIOR}，区分留给阶段2实测",
            ]
            source = []
        # license 偏好仅作排序加成，不进核心公式(plan §2)
        lic = "open-weights" if e["backend"] == "ollama" else "api"
        if prefer_license and lic == prefer_license:
            score = round(min(1.0, score + 0.05), 3)
            why.append(f"--prefer-license {prefer_license} 命中 → +0.05 排序加成")
        cands.append({
            "model": name, "testable": True, "mapped_local": name,
            "license": lic, "size_b": size, "theory_score": round(score, 3),
            "tier": tier_of(score), "why": why, "source_entries": source,
        })
    return cands


def build_ceilings(champs, domain, axis):
    """闭源标杆 → 天花板参照(testable=false)。仅取场景相关(relevance≥0.5)且非本地族。"""
    seen, out = set(), []
    for c in sorted(champs, key=lambda x: x["base_score"], reverse=True):
        if c["relevance"] < 0.5 or c["model"] in seen:
            continue
        if c["family"] in ("qwen", "llama", "glm", "baichuan", "minimind", "safemed"):
            continue  # 本地族，归入候选而非天花板
        seen.add(c["model"])
        out.append({
            "model": c["model"], "testable": False, "mapped_local": None,
            "license": c["license"], "theory_score": c["base_score"],
            "tier": "ceiling-ref",
            "why": [f"{c['entry']} axis={c['axis']} rank={c['rank']}，闭源前沿，"
                    f"作天花板参照(不入实测队列)；专家信心={c['confidence']}"],
            "source_entries": [c["entry"]],
        })
    return out


def tier_of(score):
    if score >= TIER_MUST:
        return "must-test"
    if score >= TIER_OPT:
        return "optional"
    return "skip"


# ── 输出 ───────────────────────────────────────────────────────────────────
def to_shortlist(domain, axis, w, cands, ceilings, top):
    # 主序 theory_score；同分时规模降序(仅 tiebreak，不进 score；null=无规模标 → 视作满血 99)
    cands = sorted(cands, key=lambda c: (c["theory_score"], c["size_b"] or 99.0),
                   reverse=True)
    # top 仅限制可测候选数；天花板参照全留(它们是参照不占考位)
    if top:
        for c in cands[top:]:
            if c["tier"] != "skip":
                c["tier"] = "skip"
                c["why"].append(f"--top {top} 截断：超出短名单上限 → 降为 skip")
    return {
        "scenario": {"domain": domain, "axis": axis, "as_of": date.today().isoformat()},
        "weights": dict(w),
        "candidates": cands,
        "ceiling_refs": ceilings,
    }


def render_md(sl):
    sc = sl["scenario"]
    lines = [f"# 理论评定 · {sc['domain']} × {sc['axis']} （{sc['as_of']}）", "",
             "> 纸面 triage，**非实测分**。证据=../agent-bench/entries/*.md frontmatter。",
             f"> 权重 {sl['weights']}。闭源冠军→天花板参照；本地模型→可实测候选。", "",
             "## 可实测候选（交阶段2 run_pool --from-shortlist）", "",
             "| 模型 | tier | theory_score | license | 依据 |", "|---|---|---|---|---|"]
    for c in sl["candidates"]:
        lines.append(f"| {c['model']} | {c['tier']} | {c['theory_score']} | "
                     f"{c['license']} | {c['why'][0]} |")
    lines += ["", "## 天花板参照（闭源，testable=false，不入实测队列）", "",
              "| 标杆 | theory_score | 出处 |", "|---|---|---|"]
    for c in sl["ceiling_refs"]:
        lines.append(f"| {c['model']} | {c['theory_score']} | {c['source_entries'][0]} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="阶段1 理论评定(零 LLM)")
    ap.add_argument("--domain", default="medical", help="场景领域(匹配 entry.domain)")
    ap.add_argument("--axis", default="agent", help="场景能力轴(匹配 models_ranked.axis)")
    ap.add_argument("--prefer-license", choices=["open-weights", "api"],
                    help="排序加成(不进核心公式)")
    ap.add_argument("--top", type=int, default=5, help="可测候选短名单上限")
    ap.add_argument("--entries", default=DEFAULT_ENTRIES, help="agent-bench entries 目录")
    ap.add_argument("--pool", default=DEFAULT_POOL, help="本地候选池 yaml")
    ap.add_argument("--out", help="写 shortlist.yaml 路径(同目录另写 screen.md)；省略=stdout")
    for k, v in SCORE_WEIGHTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=float, default=v,
                        help=f"权重 {k}(默认 {v})")
    args = ap.parse_args()

    w = {k: getattr(args, k) for k in SCORE_WEIGHTS}
    domain, axis = args.domain.lower(), args.axis.lower()

    if not os.path.isdir(args.entries):
        sys.exit(f"错误：找不到 agent-bench entries 目录：{args.entries}")
    # 复用 model_pool loader(仓库约定：bash 编排、Python 管数据)
    sys.path.insert(0, os.path.join(ROOT_DIR, "bin"))
    from model_pool import load_pool
    try:
        pool = [e for e in load_pool(args.pool) if e["enabled"]]
    except (OSError, ValueError, yaml.YAMLError) as e:
        sys.exit(f"错误：模型池加载失败：{e}")

    entries = load_entries(args.entries)
    champs = collect_champions(entries, domain, axis, w)
    cands = build_candidates(pool, champs, args.prefer_license)
    ceilings = build_ceilings(champs, domain, axis)
    sl = to_shortlist(domain, axis, w, cands, ceilings, args.top)

    if not args.out:
        yaml.safe_dump(sl, sys.stdout, allow_unicode=True, sort_keys=False)
        return
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        yaml.safe_dump(sl, f, allow_unicode=True, sort_keys=False)
    md = os.path.join(os.path.dirname(args.out), "screen.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(render_md(sl))
    print(f"✓ 写 {args.out}（{len(cands)} 候选 + {len(ceilings)} 天花板参照）")
    print(f"✓ 写 {md}")


if __name__ == "__main__":
    main()
