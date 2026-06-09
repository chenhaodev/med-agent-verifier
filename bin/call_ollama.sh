#!/usr/bin/env bash
# call_ollama.sh — 调用本地 Ollama 生成（被测候选模型 system-under-test）
# 用法：printf '%s' '<prompt 文本>' | ./bin/call_ollama.sh [--model M] [--think on|off] [--no-cache]
# 输出：模型回复纯文本（thinking 模型也仅取最终 response，不含 think 轨迹）。
#
# 思考开关（DUT thinking）：--think on|off（或 OLLAMA_THINK=on|off）。
#   设为 on/off 时在 payload 注入 "think": true/false（Ollama 原生字段，对推理模型如 qwen3.5 生效）；
#   不设则省略该字段，用模型默认。off 可把 qwen3.5 从 ~147s/条 提到 ~15s/条（代价：欠测推理类任务）。
#   注意：对不支持思考的模型传 think 可能报错——故默认省略，仅显式设置时才注入。
#
# 响应缓存（默认开，对齐 call_judge.sh 行为）：
#   按 {model, prompt, think} payload 的 sha256 做内容寻址磁盘缓存（.cache/ollama/<sha>.txt）。
#   think 进 payload → 开/关思考各有独立缓存键，互不污染。
#   绕过：NO_CACHE=1 环境变量 或 --no-cache。清理：rm -rf .cache/ollama
#
# 确定性：temperature=0、stream=false → 同一 (model, prompt, think) 给定服务端稳定即复现。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:1.5b}"
OLLAMA_TIMEOUT="${OLLAMA_TIMEOUT:-300}"
OLLAMA_THINK="${OLLAMA_THINK:-}"   # ""=模型默认（省略字段）；on|off=注入 think true|false
NO_CACHE="${NO_CACHE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)    OLLAMA_MODEL="$2"; shift 2 ;;
    --think)    OLLAMA_THINK="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1;        shift ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

# 归一化 think 取值（tr 小写：兼容 macOS bash 3.2，无 ${var,,}）
THINK_LC=$(printf '%s' "$OLLAMA_THINK" | tr '[:upper:]' '[:lower:]')
case "$THINK_LC" in
  on|true|1)   THINK_VAL="true" ;;
  off|false|0) THINK_VAL="false" ;;
  "")          THINK_VAL="" ;;
  *) echo "错误：--think 取值须为 on|off（收到：$OLLAMA_THINK）" >&2; exit 1 ;;
esac

CACHE_DIR="$ROOT_DIR/.cache/ollama"

PROMPT="$(cat)"
if [[ -z "${PROMPT// /}" ]]; then
  echo "错误：call_ollama.sh 未收到 prompt（stdin 为空）。" >&2
  exit 1
fi

# ─── 安全构造 JSON payload（python 转义，避免换行/引号破坏 JSON）─────
PAYLOAD=$(MODEL="$OLLAMA_MODEL" PROMPT="$PROMPT" THINK_VAL="$THINK_VAL" python3 - <<'PYEOF'
import json, os
payload = {
    "model": os.environ["MODEL"],
    "prompt": os.environ["PROMPT"],
    "stream": False,
    "options": {"temperature": 0},
}
think = os.environ.get("THINK_VAL", "")
if think == "true":
    payload["think"] = True
elif think == "false":
    payload["think"] = False
# 未设则省略 think，用模型默认（对非思考模型避免报错）
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)

# ─── 缓存读取（命中即零网络）────────────────────────────────────────
CACHE_FILE=""
if [[ "$NO_CACHE" != "1" ]]; then
  CACHE_KEY=$(printf '%s' "$PAYLOAD" | shasum -a 256 2>/dev/null | cut -d' ' -f1) || CACHE_KEY=""
  if [[ -n "$CACHE_KEY" ]]; then
    CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.txt"
    if [[ -s "$CACHE_FILE" ]]; then
      cat "$CACHE_FILE"
      exit 0
    fi
  fi
fi

HTTP_RESPONSE=$(curl -s -w "\n__HTTP_STATUS__%{http_code}" \
  --max-time "$OLLAMA_TIMEOUT" \
  -X POST "$OLLAMA_HOST/api/generate" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" 2>&1) || {
  echo "错误：curl 请求 Ollama 失败（${OLLAMA_HOST} 未运行？）。" >&2
  exit 1
}

HTTP_BODY="$(echo "$HTTP_RESPONSE" | sed '$d')"
HTTP_STATUS="$(echo "$HTTP_RESPONSE" | tail -1 | sed 's/__HTTP_STATUS__//')"

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "错误：Ollama 返回 HTTP ${HTTP_STATUS}。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
fi

CONTENT=$(echo "$HTTP_BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
c = d.get('response', '')
if not c or not c.strip():
    print('错误：Ollama 返回空 response', file=sys.stderr)
    sys.exit(1)
print(c)
" 2>&1) || {
  echo "错误：解析 Ollama 响应失败或 response 为空。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
}

if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  printf '%s\n' "$CONTENT" > "$CACHE_FILE" 2>/dev/null || true
fi

echo "$CONTENT"
