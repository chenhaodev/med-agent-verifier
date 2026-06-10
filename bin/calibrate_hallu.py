#!/usr/bin/env python3
"""calibrate_hallu.py — 标定幻觉判官的检测准确度（对标 MedHallu 二元检测度量）。

把 eval/calibration/hallu_gold.yaml 里**人工标注 gold** 的原子声明逐条喂给
judge_prompt_hallu.md 判官，比对 judge 判的 verdict 与 gold，算出：
  - 「unsupported 检测」的 precision / recall / F1（正类=幻觉；MedHallu 的核心度量）
  - 三分类准确率、混淆矩阵
  - not_sure 子集上判官的弃权行为
这是回答「评价标准够不够好」的**证据**：不仅指标可引用，且测过它判得准不准。

判官调用走 bin/call_judge.sh（sha256 缓存）；首跑付费，重跑命中缓存近乎零成本。

用法：
  python3 bin/calibrate_hallu.py                 # 跑全集，文本报告
  python3 bin/calibrate_hallu.py --md            # Markdown
  python3 bin/calibrate_hallu.py --no-cache      # 绕过缓存重判
  python3 bin/calibrate_hallu.py --limit 4       # 只跑前 N 条（冒烟）
"""
import argparse
import json
import os
import subprocess
import sys

import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
import parse_hallu  # noqa: E402

GOLD_FILE = os.path.join(ROOT, "eval", "calibration", "hallu_gold.yaml")
HALLU_PROMPT = os.path.join(ROOT, "eval", "judge_prompt_hallu.md")
REPORT_JSON = os.path.join(ROOT, "eval", "calibration", "last_report.json")
LABELS = ("supported", "unsupported", "not_sure")


def _judge_model():
    """judge 模型名：优先 env JUDGE_MODEL / DEEPSEEK_MODEL，再读 .env，最后兜底默认。"""
    for k in ("JUDGE_MODEL", "DEEPSEEK_MODEL"):
        if os.environ.get(k):
            return os.environ[k]
    env = os.path.join(ROOT, ".env")
    if os.path.exists(env):
        with open(env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_MODEL="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        return v
    return "deepseek-v4-flash"


def judge_claim(claim, context, system, model, no_cache):
    """单条声明 → hallu 判官 → parse_hallu 结果（dict）或 None。"""
    payload = {
        "model": model, "temperature": 0, "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(
                {"question": context, "model_response": claim}, ensure_ascii=False)},
        ],
    }
    cmd = [os.path.join(SCRIPT_DIR, "call_judge.sh")]
    if no_cache:
        cmd.append("--no-cache")
    try:
        raw = subprocess.run(
            cmd, input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True, timeout=180,
        ).stdout
    except subprocess.TimeoutExpired:
        return None
    result, ok = parse_hallu.parse(raw)
    return result if ok else None


def predicted_label(h):
    """单原子声明的 judge 判定 → 三分类标签（n 一般=1，稳健起见按计数推断）。"""
    if not h:
        return None
    if h["unsupported"] > 0:
        return "unsupported"
    if h["not_sure"] > 0 and h["supported"] == 0:
        return "not_sure"
    if h["supported"] > 0:
        return "supported"
    return None


def metrics(rows):
    """正类=unsupported 的 P/R/F1（仅 gold∈{supported,unsupported}）+ 三分类准确率 + 混淆。"""
    tp = fp = fn = tn = 0
    conf = {g: {p: 0 for p in LABELS + (None,)} for g in LABELS}
    correct3 = total3 = 0
    for r in rows:
        g, p = r["gold"], r["pred"]
        conf[g][p] = conf[g].get(p, 0) + 1
        if p is not None:
            total3 += 1
            correct3 += int(g == p)
        if g in ("supported", "unsupported"):
            pred_unsup = (p == "unsupported")
            if g == "unsupported" and pred_unsup:
                tp += 1
            elif g == "supported" and pred_unsup:
                fp += 1
            elif g == "unsupported" and not pred_unsup:
                fn += 1
            elif g == "supported" and not pred_unsup:
                tn += 1
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    if prec and rec:
        f1 = 2 * prec * rec / (prec + rec)
    else:
        f1 = 0.0 if (prec == 0 or rec == 0) else None
    return {
        "unsupported_detection": {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 3) if prec is not None else None,
            "recall": round(rec, 3) if rec is not None else None,
            "f1": round(f1, 3) if f1 is not None else None,
        },
        "accuracy_3class": round(correct3 / total3, 3) if total3 else None,
        "n_scored": total3, "confusion": conf,
    }


def _ud_line(label, m):
    ud = m["unsupported_detection"]
    return (f"  {label:<14} P {ud['precision']}  R {ud['recall']}  F1 {ud['f1']}  "
            f"(TP={ud['tp']} FP={ud['fp']} FN={ud['fn']} TN={ud['tn']})  "
            f"acc3 {m['accuracy_3class']}")


def render(rows, m, tiers, as_md):
    nl = "\n"
    title = "幻觉判官标定报告（vs hallu_gold.yaml）"
    h = ("# " + title) if as_md else title
    out = [h, "" if as_md else "═" * 60]
    out.append("unsupported 检测（正类=幻觉，对标 MedHallu；hard 层=似是而非微妙错误）：")
    out.append(_ud_line("overall", m))
    for t in sorted(tiers):
        out.append(_ud_line(t, tiers[t]))
    out.append("")
    out.append("逐条：")
    for r in rows:
        ok = "✓" if r["gold"] == r["pred"] else "✗"
        out.append(f"  {ok} [{r['tier']:<4} {r['id']:<7} {r['kind']:<24}] "
                   f"gold={r['gold']:<11} pred={r['pred']}")
        if r["gold"] != r["pred"] and r.get("note"):
            out.append(f"        judge: {r['note'][:80]}")
    if not as_md:
        out.append("═" * 60)
    return nl.join(out)


def main():
    ap = argparse.ArgumentParser(description="标定幻觉判官检测准确度（MedHallu 式）")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    with open(GOLD_FILE, encoding="utf-8") as f:
        items = (yaml.safe_load(f) or {}).get("items", [])
    if args.limit:
        items = items[: args.limit]
    with open(HALLU_PROMPT, encoding="utf-8") as f:
        system = f.read()
    model = _judge_model()

    rows = []
    for it in items:
        h = judge_claim(it["claim"], it.get("context", ""), system, model, args.no_cache)
        pred = predicted_label(h)
        note = ""
        if h and h.get("claims"):
            note = "; ".join(c.get("note", "") for c in h["claims"] if c.get("note"))[:160]
        rows.append({"id": it["id"], "kind": it.get("kind", ""),
                     "tier": it.get("tier", "easy"), "gold": it["gold"],
                     "pred": pred, "note": note})
        mark = "✓" if it["gold"] == pred else "✗"
        print(f"  [{it['id']}] {mark} gold={it['gold']} pred={pred}", file=sys.stderr)

    m = metrics(rows)
    tiers = {t: metrics([r for r in rows if r["tier"] == t])
             for t in sorted({r["tier"] for r in rows})}
    print(render(rows, m, tiers, args.md))
    os.makedirs(os.path.dirname(REPORT_JSON), exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump({"model": model, "metrics": m, "tiers": tiers, "rows": rows},
                  f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
