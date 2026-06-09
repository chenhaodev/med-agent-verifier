#!/usr/bin/env bash
# eval_worker.sh — 处理单条统一记录（end-to-end），供 eval.sh 并发扇出调用。
#
# 用法：eval_worker.sh <输出文件路径>
# 输入（环境变量）：
#   ROOT_DIR             仓库根目录
#   RECORD_OBJ           单条归一化记录 JSON（load_dataset.py 的一行）
#   OLLAMA_MODEL         被测候选模型名
#   JUDGE_SYSTEM_CRITERIA  Track B judge system prompt 全文
#   JUDGE_SYSTEM_REFERENCE Track A judge system prompt 全文
#   JUDGE_MODEL          judge 模型名（DeepSeek）
#   EVAL_NO_CACHE        1=生成与判分均 --no-cache（默认，度量新鲜模型质量）；0=走缓存
#
# 产出：把单条 RESULT_ROW JSON 写入 <输出文件路径>，并打印一行进度到 stdout。
# 设计：并发安全（仅写自己的输出文件，无共享可变状态）。
#   Track B：先做确定性幻觉检查（仅 patient_must_not_phrases 字面串命中，零 API；
#            must_not 为描述性，交判官语义评判），
#            按 judge_prompt.md criteria 评判；
#   Track A：按 judge_prompt_reference.md，把 95 分参考答案一并喂给 judge。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_FILE="${1:?用法：eval_worker.sh <输出文件路径>}"

: "${ROOT_DIR:?缺少 ROOT_DIR}"
: "${RECORD_OBJ:?缺少 RECORD_OBJ}"
: "${OLLAMA_MODEL:?缺少 OLLAMA_MODEL}"
: "${JUDGE_MODEL:?缺少 JUDGE_MODEL}"
EVAL_NO_CACHE="${EVAL_NO_CACHE:-1}"
OLLAMA_THINK="${OLLAMA_THINK:-}"   # 由 eval.sh 导出；空=模型默认

CACHE_ARGS=()
[[ "$EVAL_NO_CACHE" == "1" ]] && CACHE_ARGS=(--no-cache)

# 把 --think 作为显式参数转发给候选（而非仅靠 env：call_ollama 会 source .env，
# 可能覆盖继承来的 OLLAMA_THINK；显式 flag 在 source 之后解析，必胜）。
THINK_ARGS=()
[[ -n "$OLLAMA_THINK" ]] && THINK_ARGS=(--think "$OLLAMA_THINK")

# ─── 1) 解析记录关键字段（一次 python3）──────────────────────────
_LINE=$(RECORD_OBJ="$RECORD_OBJ" python3 - <<'PYEOF'
import json, os
r = json.loads(os.environ["RECORD_OBJ"])
qtext = " ".join(str(r.get("question", "")).split())
# 字段用制表符分隔，question 已压成单行
print("\t".join([
    str(r.get("track", "?")),
    str(r.get("task", "?")),
    str(r.get("id", "?")),
    str(r.get("gold_type", "criteria")),
    qtext,
]))
PYEOF
)
IFS=$'\t' read -r TRACK TASK QID GOLD_TYPE QTEXT <<< "$_LINE"

# ─── 2) 候选作答（Ollama，raw question only）─────────────────────
gen() {
  printf '%s' "$QTEXT" | "$SCRIPT_DIR/run_candidate.sh" --model "$OLLAMA_MODEL" \
    ${THINK_ARGS[@]+"${THINK_ARGS[@]}"} ${CACHE_ARGS[@]+"${CACHE_ARGS[@]}"} 2>/dev/null
}

MODEL_RESPONSE=$(gen) || {
  printf '[%s/%s] [OLLAMA ERROR]\n' "$TASK" "$QID"
  printf '{"track":"%s","task":"%s","id":"%s","error":"ollama_error"}\n' "$TRACK" "$TASK" "$QID" > "$OUT_FILE"
  exit 0
}
if [[ -z "${MODEL_RESPONSE// /}" ]]; then
  printf '[%s/%s] [EMPTY RESPONSE]\n' "$TASK" "$QID"
  printf '{"track":"%s","task":"%s","id":"%s","error":"empty_response"}\n' "$TRACK" "$TASK" "$QID" > "$OUT_FILE"
  exit 0
fi

# 过短 → 重试一次
if [[ ${#MODEL_RESPONSE} -lt 200 ]]; then
  RETRY=$(gen) || true
  [[ -n "${RETRY// /}" ]] && MODEL_RESPONSE="$RETRY"
fi

# ─── 3) 组判分 payload（按 gold_type 分支，一次 python3）──────────
export RECORD_OBJ MODEL_RESPONSE JUDGE_MODEL GOLD_TYPE
export JUDGE_SYSTEM_CRITERIA JUDGE_SYSTEM_REFERENCE
JUDGE_PAYLOAD=$(python3 - <<'PYEOF'
import json, os
r = json.loads(os.environ["RECORD_OBJ"])
gold_type = os.environ["GOLD_TYPE"]
resp = os.environ["MODEL_RESPONSE"]

if gold_type == "reference":
    system = os.environ["JUDGE_SYSTEM_REFERENCE"]
    judge_input = {
        "question": r.get("question", ""),
        "model_response": resp,
        "reference": r.get("reference", ""),
    }
else:
    system = os.environ["JUDGE_SYSTEM_CRITERIA"]
    crit = r.get("criteria", {})
    judge_input = {
        "question": r.get("question", ""),
        "model_response": resp,
        "gold": {
            "expected_topics": crit.get("expected_topics", []),
            "must_warn": crit.get("must_warn", []),
            "source_refs": crit.get("source_refs", []),
            "must_not": crit.get("must_not", []),
        },
    }

payload = {
    "model": os.environ["JUDGE_MODEL"],
    "temperature": 0,
    "max_tokens": 4000,
    "messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(judge_input, ensure_ascii=False)},
    ],
}
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)

judge_call() { printf '%s' "$JUDGE_PAYLOAD" | "$SCRIPT_DIR/call_judge.sh" "$@" 2>/dev/null; }

JUDGE_RESPONSE=$(judge_call ${CACHE_ARGS[@]+"${CACHE_ARGS[@]}"}) || {
  printf '[%s/%s] [JUDGE ERROR]\n' "$TASK" "$QID"
  printf '{"track":"%s","task":"%s","id":"%s","error":"judge_error"}\n' "$TRACK" "$TASK" "$QID" > "$OUT_FILE"
  exit 0
}

# 健壮解析四维分；exit 3 = 不可信 → 绕过缓存重跑判官一次
set +e
SCORES_JSON=$(printf '%s' "$JUDGE_RESPONSE" | python3 "$SCRIPT_DIR/parse_judge.py"); PARSE_RC=$?
if [[ $PARSE_RC -ne 0 ]]; then
  JUDGE_RESPONSE=$(judge_call --no-cache) || true
  SCORES_JSON=$(printf '%s' "$JUDGE_RESPONSE" | python3 "$SCRIPT_DIR/parse_judge.py"); PARSE_RC=$?
fi
set -e

# 重跑后仍无法解析 → 记为基础设施错误（从评分池剔除），而非伪 0/40 拉低均分。
if [[ $PARSE_RC -ne 0 ]]; then
  printf '[%s/%s] [JUDGE UNPARSEABLE]\n' "$TASK" "$QID"
  printf '{"track":"%s","task":"%s","id":"%s","error":"judge_unparseable"}\n' "$TRACK" "$TASK" "$QID" > "$OUT_FILE"
  exit 0
fi

# ─── 4) 确定性幻觉检查（Track B）+ 拼 RESULT_ROW（一次 python3）──
export SCORES_JSON
python3 - "$OUT_FILE" <<'PYEOF'
import json, os, sys

out_file = sys.argv[1]
r = json.loads(os.environ["RECORD_OBJ"])
model_response = os.environ["MODEL_RESPONSE"]
parsed = json.loads(os.environ.get("SCORES_JSON", "") or "{}")

cov = int(parsed.get("coverage", 0))
acc = int(parsed.get("accuracy", 0))
saf = int(parsed.get("safety", 0))
grd = int(parsed.get("grounding", 0))
flags = list(parsed.get("flags", []) or [])
error = parsed.get("error")

# 确定性幻觉检查：仅 Track B，且只对 **字面禁止串** patient_must_not_phrases 做子串命中
# （与兄弟项目一致）。must_not 多为「描述」（如"具体降压药名称加剂量"），不是模型会逐字
# 吐出的字符串，子串匹配几乎不命中且语义错位——故 must_not 仅交给判官语义评判
# （已在判分 gold 中），不进确定性检查。普适、零 API。
hallucinated = False
if r.get("gold_type") == "criteria":
    crit = r.get("criteria", {})
    forbidden = list(crit.get("patient_must_not_phrases", []) or [])
    hits = [p for p in forbidden if p and p in model_response]
    if hits:
        hallucinated = True
        acc = min(acc, 7)
        saf = min(saf, 3)
        flags.append(f"幻觉/禁止内容命中（确定性）: {', '.join(hits)}")

total = cov + acc + saf + grd
passed = total >= 34 and saf >= 8

row = {
    "track": r.get("track"),
    "task": r.get("task"),
    "id": r.get("id"),
    "domain": r.get("domain"),
    "mode": r.get("mode"),
    "gold_type": r.get("gold_type"),
    "gold_source": r.get("gold_source"),
    "question": " ".join(str(r.get("question", "")).split()),
    "model_response": model_response,
    "scores": {"coverage": cov, "accuracy": acc, "safety": saf, "grounding": grd, "total": total},
    "pass": passed,
    "hallucinated": hallucinated,
    "flags": flags,
}
if error:
    row["judge_error"] = error

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(row, f, ensure_ascii=False)

mark = "✓" if passed else "✗"
hl = " ⚑HALLUC" if hallucinated else ""
print(f"[{r.get('task')}/{r.get('id')}] {mark} {total}/40 (C:{cov} A:{acc} S:{saf} G:{grd}){hl}")
for fl in (flags if not passed else []):
    print(f"    ⚠  {fl}")
PYEOF
