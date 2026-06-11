#!/usr/bin/env bash
# call_openai_compat.sh — 调用任意 OpenAI-compatible /v1/chat/completions 端点（被测候选）
# 覆盖：OpenAI 官方、siliconflow.cn、LiteLLM proxy 等一切兼容协议的服务。
# 用法：printf '%s' '<prompt>' | ./bin/call_openai_compat.sh \
#         [--model M] [--base-url URL] [--api-key-env VAR] [--think on|off] [--no-cache]
# 输出：choices[0].message.content 纯文本（reasoning 模型仅取最终 content，不含思考轨迹）。
#
# 设计对齐 call_ollama.sh：
#   · temperature=0、stream=false → 确定性优先
#   · sha256 内容寻址缓存（.cache/openai_compat/<sha>.txt；键含 base_url，同名模型跨端点不串）
#   · --think on|off 显式设置时注入 "enable_thinking"（siliconflow/Qwen 系约定；
#     不支持该字段的端点可能报 400——故默认省略，仅显式设置才注入，与 ollama 同一取舍）
#   · API key 经 --api-key-env 间接传递（只传变量名，不传值；key 不进缓存键、不进日志）
#
# 环境变量（.env 可配）：OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL / OPENAI_TIMEOUT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}"
MODEL="${OPENAI_MODEL:-}"
API_KEY_ENV="OPENAI_API_KEY"
TIMEOUT="${OPENAI_TIMEOUT:-300}"
THINK="${OPENAI_THINK:-}"          # ""=省略字段；on|off=注入 enable_thinking true|false
NO_CACHE="${NO_CACHE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)       MODEL="$2";       shift 2 ;;
    --base-url)    BASE_URL="$2";    shift 2 ;;
    --api-key-env) API_KEY_ENV="$2"; shift 2 ;;
    --think)       THINK="$2";       shift 2 ;;
    --no-cache)    NO_CACHE=1;       shift ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

if [[ -z "$MODEL" ]]; then
  echo "错误：openai-compat 后端需要模型名（--model 或 .env OPENAI_MODEL）。" >&2
  exit 1
fi

# key 按变量名间接取（litellm 本地代理可无鉴权 → 允许为空，仅在非空时发 Authorization 头）
API_KEY="${!API_KEY_ENV:-}"

# 归一化 think 取值（tr 小写：兼容 macOS bash 3.2）
THINK_LC=$(printf '%s' "$THINK" | tr '[:upper:]' '[:lower:]')
case "$THINK_LC" in
  on|true|1)   THINK_VAL="true" ;;
  off|false|0) THINK_VAL="false" ;;
  "")          THINK_VAL="" ;;
  *) echo "错误：--think 取值须为 on|off（收到：$THINK）" >&2; exit 1 ;;
esac

CACHE_DIR="$ROOT_DIR/.cache/openai_compat"

PROMPT="$(cat)"
if [[ -z "${PROMPT// /}" ]]; then
  echo "错误：call_openai_compat.sh 未收到 prompt（stdin 为空）。" >&2
  exit 1
fi

# ─── 安全构造 JSON payload（python 转义，避免换行/引号破坏 JSON）─────
PAYLOAD=$(MODEL="$MODEL" PROMPT="$PROMPT" THINK_VAL="$THINK_VAL" python3 - <<'PYEOF'
import json, os
payload = {
    "model": os.environ["MODEL"],
    "messages": [{"role": "user", "content": os.environ["PROMPT"]}],
    "temperature": 0,
    "stream": False,
}
think = os.environ.get("THINK_VAL", "")
if think == "true":
    payload["enable_thinking"] = True
elif think == "false":
    payload["enable_thinking"] = False
# 未设则省略，避免严格端点对未知字段报 400
print(json.dumps(payload, ensure_ascii=False))
PYEOF
)

# ─── 缓存读取（键含 base_url；key 本身不参与哈希）──────────────────
CACHE_FILE=""
if [[ "$NO_CACHE" != "1" ]]; then
  CACHE_KEY=$(printf '%s\n%s' "$BASE_URL" "$PAYLOAD" | shasum -a 256 2>/dev/null | cut -d' ' -f1) \
    || CACHE_KEY=""
  if [[ -n "$CACHE_KEY" ]]; then
    CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.txt"
    if [[ -s "$CACHE_FILE" ]]; then
      cat "$CACHE_FILE"
      exit 0
    fi
  fi
fi

AUTH_ARGS=()
[[ -n "$API_KEY" ]] && AUTH_ARGS=(-H "Authorization: Bearer $API_KEY")

HTTP_RESPONSE=$(curl -s -w "\n__HTTP_STATUS__%{http_code}" \
  --max-time "$TIMEOUT" \
  -X POST "${BASE_URL%/}/chat/completions" \
  -H "Content-Type: application/json" \
  ${AUTH_ARGS[@]+"${AUTH_ARGS[@]}"} \
  -d "$PAYLOAD" 2>&1) || {
  echo "错误：curl 请求失败（${BASE_URL} 不可达？）。" >&2
  exit 1
}

HTTP_BODY="$(echo "$HTTP_RESPONSE" | sed '$d')"
HTTP_STATUS="$(echo "$HTTP_RESPONSE" | tail -1 | sed 's/__HTTP_STATUS__//')"

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "错误：${BASE_URL} 返回 HTTP ${HTTP_STATUS}。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
fi

CONTENT=$(echo "$HTTP_BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
choices = d.get('choices') or []
c = (choices[0].get('message') or {}).get('content', '') if choices else ''
if not c or not c.strip():
    print('错误：端点返回空 content', file=sys.stderr)
    sys.exit(1)
print(c)
" 2>&1) || {
  echo "错误：解析 chat/completions 响应失败或 content 为空。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
}

if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  printf '%s\n' "$CONTENT" > "$CACHE_FILE" 2>/dev/null || true
fi

echo "$CONTENT"
