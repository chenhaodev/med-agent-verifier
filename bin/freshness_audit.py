#!/usr/bin/env python3
"""freshness_audit.py — gold 时效性审计（Workstream D）。**只读，从不改 gold**。

book gold 是静态的（书本 + 2024 指南快照）。本工具抽取某专科 gold 的关键要点，逐条问判官
「对照最新指南是否仍成立」，分类 current|drifted|uncertain，产出 eval/freshness/<domain>.md 报告。
更新 gold 是兄弟侧的人工决定，本工具绝不代劳（不可变性；兄弟是单一真相源）。

判官调用走 bin/call_judge.sh（bash/curl + .env），Python 只做抽取/解析/装配。
真·实时 websearch 是 /autoresearch 升级路径（由 Claude 跑 WebSearch 喂入 web 上下文）；
v1 用判官自身的指南知识做时效判断，已能产出带「最新指南」引用的 current|drifted|uncertain 报告。

用法：
  python3 bin/freshness_audit.py --domain cardiology
  python3 bin/freshness_audit.py --domain cardiology --max 8
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
FRESH_DIR = os.path.join(ROOT_DIR, "eval", "freshness")

sys.path.insert(0, SCRIPT_DIR)
from load_dataset import load_book  # noqa: E402

_VERDICTS = ("current", "drifted", "uncertain")


def extract_claims(domain, max_n):
    """从某专科 gold 抽取去重要点（expected_topics + must_warn），保留来源。"""
    seen, claims = set(), []
    for r in load_book():
        if r.get("domain") != domain:
            continue
        crit = r.get("criteria") or {}
        refs = crit.get("source_refs") or []
        for c in (crit.get("expected_topics") or []) + (crit.get("must_warn") or []):
            c = str(c).strip()
            if c and c not in seen:
                seen.add(c)
                claims.append({"claim": c, "source_refs": refs, "from_id": r.get("id")})
            if len(claims) >= max_n:
                return claims
    return claims


def judge_freshness(claim, domain, system):
    """subprocess 到 call_judge.sh 判一条要点的时效性。返回 (verdict, guideline, note)。"""
    jm = os.environ.get("JUDGE_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"
    payload = {
        "model": jm,
        "temperature": 0, "max_tokens": 800,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(
                {"domain": domain, "claim": claim["claim"], "source_refs": claim["source_refs"]},
                ensure_ascii=False)},
        ],
    }
    proc = subprocess.run(
        [os.path.join(SCRIPT_DIR, "call_judge.sh")],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True, text=True, timeout=120,
    )
    raw = proc.stdout or ""
    verdict, guideline, note = "uncertain", "unknown", ""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            v = str(d.get("verdict", "")).lower().strip()
            verdict = v if v in _VERDICTS else "uncertain"
            guideline = d.get("current_guideline") or "unknown"
            note = d.get("note") or ""
        except json.JSONDecodeError:
            pass
    return verdict, guideline, note


def main():
    ap = argparse.ArgumentParser(description="gold 时效性审计（只读）")
    ap.add_argument("--domain", required=True, help="专科（如 cardiology）")
    ap.add_argument("--max", type=int, default=10, help="审计要点数上限")
    args = ap.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("错误：未设置 DEEPSEEK_API_KEY（judge 需要）。先 source .env。")

    sys_path = os.path.join(ROOT_DIR, "eval", "judge_prompt_freshness.md")
    with open(sys_path, encoding="utf-8") as _f:
        system = _f.read()
    claims = extract_claims(args.domain, args.max)
    if not claims:
        raise SystemExit(f"该专科无 gold 要点可审计：{args.domain}")

    rows, counts = [], {v: 0 for v in _VERDICTS}
    for i, c in enumerate(claims):
        verdict, guideline, note = judge_freshness(c, args.domain, system)
        counts[verdict] += 1
        rows.append((c["claim"], verdict, guideline, note, c["from_id"]))
        mark = {"current": "✓", "drifted": "⚠", "uncertain": "?"}[verdict]
        print(f"[{i + 1}/{len(claims)}] {mark} {verdict:<9} {c['claim'][:36]}")

    os.makedirs(FRESH_DIR, exist_ok=True)
    out = os.path.join(FRESH_DIR, f"{args.domain}.md")
    badge = {"current": "✓ current", "drifted": "⚠ drifted", "uncertain": "? uncertain"}
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# gold 时效性审计 — {args.domain}（{date.today().isoformat()}）\n\n")
        f.write("> 只读报告，**不修改 gold**。judge 据其指南知识判断；真·实时 websearch 为 "
                "/autoresearch 升级路径。`drifted` 项建议人工复核并反馈到兄弟项目 gold。\n\n")
        f.write(f"摘要：current {counts['current']} · **drifted {counts['drifted']}** · "
                f"uncertain {counts['uncertain']}（共 {len(rows)}）\n\n")
        f.write("| gold 要点 | 判定 | 最新指南 | 说明 | 来源题 |\n")
        f.write("|---|---|---|---|---|\n")
        def cell(s):
            return str(s).replace("|", "\\|").replace("\n", " ")
        for claim, verdict, guideline, note, fid in rows:
            f.write(f"| {cell(claim)} | {badge[verdict]} | {cell(guideline)} | "
                    f"{cell(note)} | {cell(fid)} |\n")
    print(f"\n摘要：current {counts['current']} · drifted {counts['drifted']} · "
          f"uncertain {counts['uncertain']}")
    print(f"→ {os.path.relpath(out, ROOT_DIR)}")


if __name__ == "__main__":
    main()
