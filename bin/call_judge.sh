#!/usr/bin/env bash
# call_judge.sh — 调用 DeepSeek API 作为评判模型（judge）
# 用法：printf '%s' '<json_payload>' | ./bin/call_judge.sh [--no-cache]
# 输出：judge 回复文本（JSON 字符串，交给 parse_judge.py 解析四维分）。
#
# 复用兄弟项目 call_deepseek.sh 的健壮逻辑（指数退避重试 + sha256 内容寻址缓存），
# 仅把缓存目录改为本仓库 .cache/judge，复用同一 .env（DEEPSEEK_API_KEY/MODEL/…）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

NO_CACHE="${NO_CACHE:-0}"
for arg in "$@"; do
  case "$arg" in
    --no-cache) NO_CACHE=1 ;;
  esac
done

[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
DEEPSEEK_MODEL="${DEEPSEEK_MODEL:-deepseek-v4-flash}"
DEEPSEEK_TIMEOUT="${DEEPSEEK_TIMEOUT:-60}"
DEEPSEEK_MAX_RETRIES="${DEEPSEEK_MAX_RETRIES:-3}"
API_URL="https://api.deepseek.com/v1/chat/completions"
CACHE_DIR="$ROOT_DIR/.cache/judge"

PAYLOAD="$(cat)"
if [[ -z "$PAYLOAD" ]]; then
  echo "错误：call_judge.sh 未收到 JSON payload（stdin 为空）。" >&2
  exit 1
fi

# ─── 缓存读取（命中即零网络，无需 key）──────────────────────────
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

if [[ -z "$DEEPSEEK_API_KEY" ]]; then
  echo "错误：未设置 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填入 key。" >&2
  exit 1
fi

attempt=0
while true; do
  attempt=$((attempt + 1))

  HTTP_RESPONSE=$(curl -s -w "\n__HTTP_STATUS__%{http_code}" \
    --max-time "$DEEPSEEK_TIMEOUT" \
    -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
    -d "$PAYLOAD" 2>&1) || {
    echo "错误：curl 请求失败（网络问题或超时）。" >&2
    exit 1
  }

  HTTP_BODY="$(echo "$HTTP_RESPONSE" | sed '$d')"
  HTTP_STATUS="$(echo "$HTTP_RESPONSE" | tail -1 | sed 's/__HTTP_STATUS__//')"

  if [[ "$HTTP_STATUS" == "200" ]]; then
    CONTENT=$(echo "$HTTP_BODY" | python3 -c "
import sys, json
data = json.load(sys.stdin)
content = data['choices'][0]['message']['content']
if not content or not content.strip():
    print('错误：API 返回空 content', file=sys.stderr)
    sys.exit(1)
print(content)
" 2>&1) || {
      echo "错误：解析 API 响应失败或 content 为空，将重试。" >&2
      if [[ $attempt -ge $DEEPSEEK_MAX_RETRIES ]]; then
        echo "响应：$HTTP_BODY" >&2
        exit 1
      fi
      sleep $((attempt * 2))
      continue
    }
    if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
      mkdir -p "$CACHE_DIR" 2>/dev/null || true
      printf '%s\n' "$CONTENT" > "$CACHE_FILE" 2>/dev/null || true
    fi
    echo "$CONTENT"
    exit 0
  fi

  if [[ "$HTTP_STATUS" == "429" || "$HTTP_STATUS" == "500" || "$HTTP_STATUS" == "502" || "$HTTP_STATUS" == "503" ]]; then
    if [[ $attempt -ge $DEEPSEEK_MAX_RETRIES ]]; then
      echo "错误：API 返回 HTTP ${HTTP_STATUS}，已重试 $attempt 次，放弃。" >&2
      echo "响应：$HTTP_BODY" >&2
      exit 1
    fi
    SLEEP_SEC=$((attempt * 2))
    echo "警告：HTTP ${HTTP_STATUS}，${SLEEP_SEC}s 后重试（第 ${attempt}/${DEEPSEEK_MAX_RETRIES} 次）..." >&2
    sleep "$SLEEP_SEC"
    continue
  fi

  echo "错误：API 返回 HTTP ${HTTP_STATUS}。" >&2
  echo "响应：$HTTP_BODY" >&2
  exit 1
done
