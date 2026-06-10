#!/usr/bin/env bash
# eval.sh — med-agent-verifier 全量评估（并发版，两路 gold 统一管线）
#
# 用法：
#   ./bin/eval.sh --track book --domain cardiology --limit 3 --model qwen3.5
#   ./bin/eval.sh --track medbench --task MedShield --limit 3 --model qwen3.5
#   ./bin/eval.sh --track both --sample 2 --model qwen3.5
#
# 选项：
#   --subset mini|medium|large   命名分层子集（eval/subsets/*.yaml，由 select_subset.py 生成）
#   --track book|medbench|both   --task T1,T2   --domain S1,S2（仅 Track B）
#   --id ID   --limit N   --sample N（每 task/domain 前 N 条）
#   --model M（被测 Ollama 模型）   --think on|off（DUT 思考开关，默认随模型）
#   --judge-model M（DeepSeek judge）
#   --concurrency N（默认 1：本地 GPU 串行，候选+判官耦合在 worker 内）
#   --cache（生成与判分走缓存，快速迭代；默认不走缓存，度量新鲜质量）
#
# 度量：每条记录 → 候选作答 → (Track B 确定性幻觉检查) → judge → 四维分。
# 汇总：总体通过率 + 四维均分；Track B 额外出每专科表 + 幻觉率；Track A 出每 task 能力分。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# .env 先于默认值与参数解析加载：让 .env 作为默认的回退源（via :-），
# 而命令行 --model/--judge-model 等显式参数仍能覆盖它（修：旧版在解析后 source
# 会用 .env 的 OLLAMA_MODEL 反向覆盖 --model）。
[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

TRACK="both"
TASK=""; DOMAIN=""; FILTER_ID=""; LIMIT=""; SAMPLE=""; SUBSET=""
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
OLLAMA_THINK="${OLLAMA_THINK:-}"   # DUT 思考开关：on|off；空=模型默认
JUDGE_MODEL="${JUDGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"
# 默认 1：Ollama 默认 NUM_PARALLEL=1 串行处理，候选+判官耦合在 worker 内；
# 若设 >1，排队中的请求其 curl --max-time 会把排队等待一并计入而超时（实测每条 ~147s，
# 并发 2 → 两条串行 ~294s 撞 300s 超时）。GPU 真并行需另设 OLLAMA_NUM_PARALLEL 并够显存。
EVAL_CONCURRENCY="${EVAL_CONCURRENCY:-1}"
EVAL_NO_CACHE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --track)       TRACK="$2";            shift 2 ;;
    --subset)      SUBSET="$2";           shift 2 ;;
    --task)        TASK="$2";             shift 2 ;;
    --domain)      DOMAIN="$2";           shift 2 ;;
    --id)          FILTER_ID="$2";        shift 2 ;;
    --limit)       LIMIT="$2";            shift 2 ;;
    --sample)      SAMPLE="$2";           shift 2 ;;
    --model)       OLLAMA_MODEL="$2";     shift 2 ;;
    --think)       OLLAMA_THINK="$2";     shift 2 ;;
    --judge-model) JUDGE_MODEL="$2";      shift 2 ;;
    --concurrency) EVAL_CONCURRENCY="$2"; shift 2 ;;
    --cache)       EVAL_NO_CACHE=0;       shift ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY（judge 需要）。复制 .env.example → .env 填 key。" >&2
  exit 1
fi

RESULTS_DIR="$ROOT_DIR/eval/results"
mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
# 子集名进文件名 → mini/medium/large 跑互不覆盖、可区分
LABEL="$TRACK"; [[ -n "$SUBSET" ]] && LABEL="${SUBSET}_${TRACK}"
RESULT_FILE="$RESULTS_DIR/${TIMESTAMP}_${LABEL}.json"
SUMMARY_FILE="$RESULTS_DIR/${TIMESTAMP}_${LABEL}_summary.txt"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " med-agent-verifier Eval — $(date '+%Y-%m-%d %H:%M:%S')"
echo " track=${TRACK}${SUBSET:+  subset=${SUBSET}}  model=${OLLAMA_MODEL}  think=${OLLAMA_THINK:-default}  judge=${JUDGE_MODEL}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

# ─── 选题：load_dataset.py 归一化 + 过滤 → 每条 q_NNNN.json ──────
LOADER_ARGS=(--track "$TRACK")
[[ -n "$SUBSET" ]]    && LOADER_ARGS+=(--subset "$SUBSET")
[[ -n "$TASK" ]]      && LOADER_ARGS+=(--task "$TASK")
[[ -n "$DOMAIN" ]]    && LOADER_ARGS+=(--domain "$DOMAIN")
[[ -n "$FILTER_ID" ]] && LOADER_ARGS+=(--id "$FILTER_ID")
[[ -n "$LIMIT" ]]     && LOADER_ARGS+=(--limit "$LIMIT")
[[ -n "$SAMPLE" ]]    && LOADER_ARGS+=(--sample "$SAMPLE")

python3 "$SCRIPT_DIR/load_dataset.py" "${LOADER_ARGS[@]}" > "$WORKDIR/records.jsonl"

# 拆成逐条 q_NNNN.json，并让拆分器成为「条数唯一真相源」：仅对非空行用**连续计数器** n
# 命名 q_{n}，最后打印 n。这样 q 文件下标恒为 0..TOTAL-1 连续，与 dispatcher 的
# seq 0..TOTAL-1 严格对齐——杜绝 wc -l 与 enumerate 因空行/无尾换行而错位丢记录。
TOTAL=$(python3 - "$WORKDIR" <<'PYEOF'
import os, sys
workdir = sys.argv[1]
n = 0
with open(os.path.join(workdir, "records.jsonl"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        with open(os.path.join(workdir, f"q_{n:04d}.json"), "w", encoding="utf-8") as out:
            out.write(line)
        n += 1
print(n)
PYEOF
)

if [[ "$TOTAL" -eq 0 ]]; then
  echo "没有符合条件的记录，退出。" >&2
  exit 0
fi

echo "记录总数：$TOTAL  |  并发：$EVAL_CONCURRENCY  |  生成缓存：$([[ "$EVAL_NO_CACHE" == "0" ]] && echo 开 || echo 关)"
echo ""

JUDGE_SYSTEM_CRITERIA=$(cat "$ROOT_DIR/eval/judge_prompt.md")
JUDGE_SYSTEM_REFERENCE=$(cat "$ROOT_DIR/eval/judge_prompt_reference.md")
JUDGE_SYSTEM_PROBE=$(cat "$ROOT_DIR/eval/judge_prompt_probe.md")
JUDGE_SYSTEM_TIA=$(cat "$ROOT_DIR/eval/judge_prompt_tia.md")

export ROOT_DIR SCRIPT_DIR WORKDIR OLLAMA_MODEL OLLAMA_THINK JUDGE_MODEL EVAL_NO_CACHE
export JUDGE_SYSTEM_CRITERIA JUDGE_SYSTEM_REFERENCE JUDGE_SYSTEM_PROBE JUDGE_SYSTEM_TIA
export OLLAMA_HOST DEEPSEEK_API_KEY DEEPSEEK_MODEL DEEPSEEK_TIMEOUT DEEPSEEK_MAX_RETRIES 2>/dev/null || true

DISPATCHER="$WORKDIR/dispatch.sh"
cat > "$DISPATCHER" << 'DISPATCH_EOF'
#!/usr/bin/env bash
idx=$(printf '%04d' "$1")
RECORD_OBJ=$(cat "$WORKDIR/q_${idx}.json") \
  "$SCRIPT_DIR/eval_worker.sh" "$WORKDIR/r_${idx}.json"
DISPATCH_EOF
chmod +x "$DISPATCHER"

seq 0 $((TOTAL - 1)) | xargs -P "$EVAL_CONCURRENCY" -n1 "$DISPATCHER"

# ─── 聚合 → results + summary ─────────────────────────────────────
{
python3 - "$WORKDIR" "$RESULT_FILE" "$TIMESTAMP" "$TRACK" "$OLLAMA_MODEL" "$JUDGE_MODEL" "$TOTAL" "$SUBSET" <<'PYEOF'
import json, glob, sys, os
from collections import defaultdict

workdir, result_file, ts, track, model, judge_model, total = sys.argv[1:8]
subset = sys.argv[8] if len(sys.argv) > 8 else ""
total = int(total)

rows = []
for path in sorted(glob.glob(f"{workdir}/r_*.json")):
    try:
        with open(path, encoding="utf-8") as f:
            rows.append(json.load(f))
    except Exception as e:
        print(f"警告：无法读取 {path}: {e}", file=sys.stderr)

evaluated = len(rows)
errors = sum(1 for r in rows if "error" in r)
scored = [r for r in rows if "error" not in r]
# 四维评分行（reference/criteria/live）与 Accuracy 行（probe/tool_decision：success 非四维）分流
scored4 = [r for r in scored if "scores" in r]
probe_rows = [r for r in scored if "scores" not in r]  # 探针 + 工具决策等二元成功行
n = len(scored4)
passed = sum(1 for r in scored4 if r.get("pass") is True)
halluc = sum(1 for r in scored4 if r.get("hallucinated") is True)

def avg(key):
    return round(sum(r["scores"][key] for r in scored4) / n, 1) if n else 0

pass_rate = round(passed * 100 / n, 1) if n else 0
halluc_rate = round(halluc * 100 / n, 1) if n else 0

# 每专科（Track B）
by_domain = defaultdict(list)
for r in scored4:
    if r.get("domain"):
        by_domain[r["domain"]].append(r)
domain_table = {
    d: {
        "n": len(rs),
        "avg_total": round(sum(x["scores"]["total"] for x in rs) / len(rs), 1),
        "hallucinated": sum(1 for x in rs if x.get("hallucinated")),
    }
    for d, rs in sorted(by_domain.items())
}

# 每 task（Track A 能力分）
by_task = defaultdict(list)
for r in scored4:
    by_task[r.get("task")].append(r)
task_table = {
    t: {"n": len(rs), "avg_total": round(sum(x["scores"]["total"] for x in rs) / len(rs), 1)}
    for t, rs in sorted(by_task.items())
}

# 每 track 均分：A(reference) 与 B(criteria) 用不同 judge 规约/标度，混在一个 headline
# 均分里不可比；故当一次跑同时含两路时，分轨各报一份，避免顶线数字随过滤构成漂移。
by_track = defaultdict(list)
for r in scored4:
    by_track[r.get("track")].append(r)
track_table = {
    tk: {
        "n": len(rs),
        **{k: round(sum(x["scores"][k] for x in rs) / len(rs), 1)
           for k in ("coverage", "accuracy", "safety", "grounding", "total")},
    }
    for tk, rs in sorted(by_track.items())
}

# Accuracy 行成功率（probe/tool_decision，按 probe_kind 或 task 分）
probe_table = {}
if probe_rows:
    by_kind = defaultdict(list)
    for r in probe_rows:
        by_kind[r.get("probe_kind") or r.get("task") or "acc"].append(r)
    probe_table = {
        k: {"n": len(rs),
            "success_rate": round(
                sum(1 for x in rs if x.get("success") or x.get("correct")) / len(rs), 3)}
        for k, rs in sorted(by_kind.items())
    }

summary = {
    "timestamp": ts, "track": track, "subset": subset or None, "model": model, "judge_model": judge_model,
    "total": total, "evaluated": evaluated, "errors": errors,
    "passed": passed, "pass_rate_pct": pass_rate,
    "hallucinated": halluc, "hallucination_rate_pct": halluc_rate,
    "avg_scores": {k: avg(k) for k in ("coverage", "accuracy", "safety", "grounding", "total")},
    "by_track": track_table,
    "by_domain": domain_table,
    "by_task": task_table,
    "by_probe": probe_table,
}
with open(result_file, "w", encoding="utf-8") as f:
    json.dump({"summary": summary, "results": rows}, f, ensure_ascii=False, indent=2)

print("\n════════════════════════════════════════════")
print(f" Eval 汇总 — {ts}  track={track}  model={model}")
print("════════════════════════════════════════════")
probe_n = len(probe_rows)
print(f" 记录：{total}  有效评分：{n}{('  探针：'+str(probe_n)) if probe_n else ''}  错误：{errors}")
if n:  # 仅当有四维评分行才报通过率/四维均分（probe-only 跑会跳过，避免全 0 噪声）
    print(f" 通过：{passed}  通过率：{pass_rate}%")
    if len(track_table) > 1:
        # 混合跑：分轨报均分（A/B 标度不可比），不报一个混合 headline
        print(" 分轨均分（A=reference / B=criteria，标度不可比，勿合并比较）：")
        for tk, v in track_table.items():
            print(f"   {tk:<9} n={v['n']:<3} C:{v['coverage']} A:{v['accuracy']} S:{v['safety']} G:{v['grounding']}  → {v['total']}/40")
    else:
        print(f" 四维均分  C:{avg('coverage')} A:{avg('accuracy')} S:{avg('safety')} G:{avg('grounding')}  → 综合 {avg('total')}/40")
if track in ("book", "both") and any(r.get("gold_type") == "criteria" for r in scored):
    print(f" 幻觉率（确定性 patient_must_not_phrases 命中）：{halluc_rate}%  （{halluc}/{n}）")
if domain_table:
    print("\n 每专科（Track B）：")
    for d, v in domain_table.items():
        hl = f"  幻觉 {v['hallucinated']}" if v["hallucinated"] else ""
        print(f"   {d:<16} n={v['n']:<3} 综合 {v['avg_total']}/40{hl}")
if track in ("medbench", "both") and any(r.get("gold_type") == "reference" for r in scored):
    print("\n 每 task 能力分（Track A，对照 95 分参考）：")
    for t, v in task_table.items():
        if any(r.get("task") == t and r.get("gold_type") == "reference" for r in scored):
            print(f"   {t:<14} n={v['n']:<3} 综合 {v['avg_total']}/40")
if probe_table:
    print(f"\n Accuracy 度量成功率（探针纠偏/拒答、工具决策正确=成功，{len(probe_rows)} 条）：")
    for k, v in probe_table.items():
        print(f"   {k:<18} n={v['n']:<3} 成功率 {v['success_rate']*100:.0f}%")
print("════════════════════════════════════════════")
print(f" 结果文件：{result_file}")
PYEOF
} | tee "$SUMMARY_FILE"
