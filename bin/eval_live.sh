#!/usr/bin/env bash
# eval_live.sh — 动态评测：对**任意**疾病问题，用兄弟 Agent 的现答作参考，评候选模型（Workstream C）。
#
# 用法：
#   echo "我爸有高血压，平时饮食要注意什么？" | ./bin/eval_live.sh --agent internists --model qwen3.5
#   ./bin/eval_live.sh --agent internists --model qwen3.5 --file questions.txt   # 每行一个问题
#   ./bin/eval_live.sh --agent psy --mode patient --model qwen3.5 "我最近总是失眠怎么办？"
#
# 选项：--agent internists|psy  --mode patient|doctor  --model M  --think on|off  --no-cache
#       --file F（每行一问）；或把单个问题作为位置参数 / stdin。
#
# 流程（每题）：run_sibling 取现答(reference) → 组 gold_type=reference 记录(track=live)
#   → 复用 eval_worker.sh 的 reference 判分管线 → 候选答 vs 兄弟现答四维分 → 写 live_ 结果。
# 兄弟现答=可信但静态（书本+2024 指南、DeepSeek 生成），故本路测「与书本 Agent 的一致性」，非真值。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

AGENT="internists"; MODE="patient"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
OLLAMA_THINK="${OLLAMA_THINK:-}"
JUDGE_MODEL="${JUDGE_MODEL:-${DEEPSEEK_MODEL:-deepseek-v4-flash}}"
EVAL_NO_CACHE=1
FILE=""; POS_Q=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)    AGENT="$2";        shift 2 ;;
    --mode)     MODE="$2";         shift 2 ;;
    --model)    OLLAMA_MODEL="$2"; shift 2 ;;
    --think)    OLLAMA_THINK="$2"; shift 2 ;;
    --judge-model) JUDGE_MODEL="$2"; shift 2 ;;
    --file)     FILE="$2";         shift 2 ;;
    --cache)    EVAL_NO_CACHE=0;   shift ;;
    --no-cache) EVAL_NO_CACHE=1;   shift ;;
    -*) echo "未知参数：$1" >&2; exit 1 ;;
    *)  POS_Q="$1";                shift ;;
  esac
done

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY（judge 需要）。" >&2; exit 1
fi

# 收集问题：--file 优先，否则位置参数，否则 stdin
QUESTIONS=()
if [[ -n "$FILE" ]]; then
  [[ -f "$FILE" ]] || { echo "错误：--file 不存在：$FILE" >&2; exit 1; }
  while IFS= read -r line; do [[ -n "${line// /}" ]] && QUESTIONS+=("$line"); done < "$FILE"
elif [[ -n "$POS_Q" ]]; then
  QUESTIONS+=("$POS_Q")
else
  STDIN_Q="$(cat)"
  [[ -n "${STDIN_Q// /}" ]] && QUESTIONS+=("$STDIN_Q")
fi
[[ ${#QUESTIONS[@]} -gt 0 ]] || { echo "错误：未提供问题（--file / 位置参数 / stdin）。" >&2; exit 1; }

RESULTS_DIR="$ROOT_DIR/eval/results"; mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
RESULT_FILE="$RESULTS_DIR/${TIMESTAMP}_live_${AGENT}.json"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " med-agent-verifier 动态评测（live）— $(date '+%H:%M:%S')"
echo " agent=${AGENT} mode=${MODE}  model=${OLLAMA_MODEL}  judge=${JUDGE_MODEL}  题数=${#QUESTIONS[@]}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

WORKDIR=$(mktemp -d); trap 'rm -rf "$WORKDIR"' EXIT
JUDGE_SYSTEM_REFERENCE=$(cat "$ROOT_DIR/eval/judge_prompt_reference.md")
export ROOT_DIR OLLAMA_MODEL OLLAMA_THINK JUDGE_MODEL EVAL_NO_CACHE JUDGE_SYSTEM_REFERENCE
export OLLAMA_HOST DEEPSEEK_API_KEY DEEPSEEK_MODEL DEEPSEEK_TIMEOUT DEEPSEEK_MAX_RETRIES 2>/dev/null || true
NC_ARGS=(); [[ "$EVAL_NO_CACHE" == "1" ]] && NC_ARGS=(--no-cache)

i=0
for Q in "${QUESTIONS[@]}"; do
  idx=$(printf '%04d' "$i")
  # 1) 兄弟现答（reference）
  REF=$(printf '%s' "$Q" | "$SCRIPT_DIR/run_sibling.sh" --agent "$AGENT" --mode "$MODE" \
        ${NC_ARGS[@]+"${NC_ARGS[@]}"}) || {
    echo "[live/$i] [SIBLING ERROR] 跳过"; i=$((i + 1)); continue
  }
  # 2) 组 reference 记录（track=live）→ eval_worker reference 判分管线
  RECORD_OBJ=$(Q="$Q" REF="$REF" AGENT="$AGENT" IDX="$i" python3 - <<'PYEOF'
import json, os
print(json.dumps({
    "track": "live", "task": "live", "id": f"LIVE_{int(os.environ['IDX']):04d}",
    "domain": None, "mode": None, "gold_type": "reference", "metric": "judge",
    "question": os.environ["Q"], "reference": os.environ["REF"],
    "gold_source": f"live:{os.environ['AGENT']}",
}, ensure_ascii=False))
PYEOF
)
  RECORD_OBJ="$RECORD_OBJ" "$SCRIPT_DIR/eval_worker.sh" "$WORKDIR/r_${idx}.json" || true
  i=$((i + 1))
done

# 3) 聚合
python3 - "$WORKDIR" "$RESULT_FILE" "$TIMESTAMP" "$OLLAMA_MODEL" "$JUDGE_MODEL" "$AGENT" <<'PYEOF'
import json, glob, sys
workdir, result_file, ts, model, judge_model, agent = sys.argv[1:7]
rows = []
for path in sorted(glob.glob(f"{workdir}/r_*.json")):
    with open(path, encoding="utf-8") as f:
        rows.append(json.load(f))
scored = [r for r in rows if "error" not in r and "scores" in r]
n = len(scored)
avg = (lambda k: round(sum(r["scores"][k] for r in scored) / n, 1) if n else 0)
summary = {
    "timestamp": ts, "track": "live", "subset": None, "agent": agent,
    "model": model, "judge_model": judge_model,
    "total": len(rows), "evaluated": n, "errors": sum(1 for r in rows if "error" in r),
    "avg_scores": {k: avg(k) for k in ("coverage", "accuracy", "safety", "grounding", "total")},
}
with open(result_file, "w", encoding="utf-8") as f:
    json.dump({"summary": summary, "results": rows}, f, ensure_ascii=False, indent=2)
print("\n════════════════════════════════════════════")
print(f" 动态评测汇总 — agent={agent} model={model}  有效 {n}/{len(rows)}")
if n:
    print(f" 四维均分  C:{avg('coverage')} A:{avg('accuracy')} S:{avg('safety')} "
          f"G:{avg('grounding')}  → 综合 {avg('total')}/40（对兄弟现答）")
print(f" 结果文件：{result_file}")
PYEOF
