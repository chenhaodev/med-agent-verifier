#!/usr/bin/env python3
"""eval_routing.py — F1 专科路由准确率（judge-free，零 DeepSeek 预算）。

「选择科室」是一项可量化的编排能力。本评测复用 book gold 的 expected_domain 作为**真值**：
让候选模型对每个问题只输出一个专科，与真值精确匹配 → Accuracy。无判官、无 API 成本。

候选调用经统一后端调度：subprocess 到 bin/call_candidate.sh（--backend ollama|openai|
siliconflow|litellm，默认 ollama 即官方 REST /api/generate），Python 只做选题/解析/计分
（与仓库「bash 编排 LLM、Python 管数据」一致）。

结果写 eval/results/{ts}_routing.json（summary+results，track=routing），供 leaderboard.py
归入 orchestration（族③）轴、场景报告（report_scenario.py）的编排稳健栏。

用法：
  python3 bin/eval_routing.py --model qwen3.5 --think off
  python3 bin/eval_routing.py --model qwen3.5 --limit 20 --domain cardiology
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
RESULTS_DIR = os.path.join(ROOT_DIR, "eval", "results")

sys.path.insert(0, SCRIPT_DIR)
from load_dataset import load_book  # noqa: E402
from parse_choice import match_choice  # noqa: E402

PROMPT_TMPL = (
    "你是医院分诊助手。请从下面的专科列表中选出与该问题**唯一最相关**的专科，"
    "只输出该专科的英文标识本身（如 cardiology），不要解释、不要标点、不要其它文字。\n"
    "专科列表：{specialties}\n"
    "问题：{question}\n"
    "专科："
)


def call_candidate(prompt, model, backend, think, no_cache):
    """subprocess 到 call_candidate.sh（后端调度）。返回回复文本；失败抛 CalledProcessError。"""
    args = [os.path.join(SCRIPT_DIR, "call_candidate.sh"),
            "--backend", backend, "--model", model]
    if think:
        args += ["--think", think]
    if no_cache:
        args += ["--no-cache"]
    proc = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, args, proc.stdout, proc.stderr)
    return proc.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description="F1 专科路由准确率（judge-free）")
    ap.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b"))
    ap.add_argument("--backend", default=os.environ.get("CANDIDATE_BACKEND", "ollama"),
                    help="候选后端：ollama|openai|siliconflow|litellm（默认 ollama）")
    ap.add_argument("--think", default="", help="on|off；空=模型默认")
    ap.add_argument("--limit", type=int, help="只评前 N 条")
    ap.add_argument("--domain", help="只评某专科（逗号分隔）")
    ap.add_argument("--cache", action="store_true", help="走候选缓存（默认不走，度量新鲜质量）")
    args = ap.parse_args()

    records = load_book()
    specialties = sorted({r["domain"] for r in records if r.get("domain")})
    if args.domain:
        doms = {d.strip() for d in args.domain.split(",")}
        records = [r for r in records if r.get("domain") in doms]
    records = [r for r in records if r.get("domain")]  # 无真值的不评
    if args.limit:
        records = records[: args.limit]
    if not records:
        print("无可评记录（book gold 为空或过滤后为空）。", file=sys.stderr)
        sys.exit(0)

    spec_str = ", ".join(specialties)
    rows, errors = [], 0
    no_cache = not args.cache
    for r in records:
        prompt = PROMPT_TMPL.format(specialties=spec_str, question=r.get("question", ""))
        try:
            resp = call_candidate(prompt, args.model, args.backend, args.think, no_cache)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            errors += 1
            rows.append({"track": "routing", "task": "specialty_routing",
                         "id": r.get("id"), "error": "candidate_error"})
            print(f"[routing/{r.get('id')}] [CANDIDATE ERROR]")
            continue
        predicted = match_choice(resp, specialties)
        correct = predicted == r.get("domain")
        rows.append({
            "track": "routing", "task": "specialty_routing", "id": r.get("id"),
            "domain": r.get("domain"), "predicted": predicted, "correct": bool(correct),
            "question": " ".join(str(r.get("question", "")).split()),
            "model_response": resp,
        })
        mark = "✓" if correct else "✗"
        print(f"[routing/{r.get('id')}] {mark} 真值={r.get('domain')} 预测={predicted}")

    scored = [x for x in rows if "error" not in x]
    n = len(scored)
    correct_n = sum(1 for x in scored if x.get("correct"))
    accuracy = round(correct_n / n, 3) if n else 0.0
    by_dom = defaultdict(list)
    for x in scored:
        by_dom[x["domain"]].append(x)
    by_domain = {
        d: {"n": len(xs), "accuracy": round(sum(1 for y in xs if y["correct"]) / len(xs), 3)}
        for d, xs in sorted(by_dom.items())
    }

    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    summary = {
        "timestamp": ts, "track": "routing", "subset": None,
        "backend": args.backend, "model": args.model, "judge_model": None,
        "total": len(records), "evaluated": n, "errors": errors,
        "accuracy": accuracy, "by_domain": by_domain,
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, f"{ts}_routing.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": rows}, f, ensure_ascii=False, indent=2)

    print(f"\n专科路由准确率：{accuracy*100:.1f}%  ({correct_n}/{n})  错误 {errors}")
    print(f"→ {os.path.relpath(out, ROOT_DIR)}")


if __name__ == "__main__":
    main()
