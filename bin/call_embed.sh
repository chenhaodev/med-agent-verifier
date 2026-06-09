#!/usr/bin/env bash
# call_embed.sh — 取一段文本的句向量（官方 Ollama embeddings，bash+curl）
# 用法：printf '%s' '<文本>' | ./bin/call_embed.sh [--model M] [--no-cache]
# 输出：一行 JSON 数组（embedding 向量），如 [0.057,1.983,...]
#
# 官方端点：POST /api/embeddings  {model, prompt}（与候选 call_ollama.sh 同源约定）。
# 缓存（默认开）：按 {model, prompt} 的 sha256 存 .cache/embed/<sha>.json，命中即零网络。
# 用途：select_subset.py 给每道题求向量，做「最正交」子集挑选（farthest-first / MMR）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text}"
OLLAMA_TIMEOUT="${OLLAMA_TIMEOUT:-300}"
NO_CACHE="${NO_CACHE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)    EMBED_MODEL="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1;       shift ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

CACHE_DIR="$ROOT_DIR/.cache/embed"

TEXT="$(cat)"
if [[ -z "${TEXT// /}" ]]; then
  echo "错误：call_embed.sh 未收到文本（stdin 为空）。" >&2
  exit 1
fi

PAYLOAD=$(MODEL="$EMBED_MODEL" TEXT="$TEXT" python3 - <<'PYEOF'
import json, os
print(json.dumps({"model": os.environ["MODEL"], "prompt": os.environ["TEXT"]}, ensure_ascii=False))
PYEOF
)

CACHE_FILE=""
if [[ "$NO_CACHE" != "1" ]]; then
  CACHE_KEY=$(printf '%s' "$PAYLOAD" | shasum -a 256 2>/dev/null | cut -d' ' -f1) || CACHE_KEY=""
  if [[ -n "$CACHE_KEY" ]]; then
    CACHE_FILE="$CACHE_DIR/${CACHE_KEY}.json"
    [[ -s "$CACHE_FILE" ]] && { cat "$CACHE_FILE"; exit 0; }
  fi
fi

HTTP_RESPONSE=$(curl -s -w "\n__HTTP_STATUS__%{http_code}" \
  --max-time "$OLLAMA_TIMEOUT" \
  -X POST "$OLLAMA_HOST/api/embeddings" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD") || { echo "错误：curl 请求 Ollama embeddings 失败。" >&2; exit 1; }

HTTP_BODY="$(echo "$HTTP_RESPONSE" | sed '$d')"
HTTP_STATUS="$(echo "$HTTP_RESPONSE" | tail -1 | sed 's/__HTTP_STATUS__//')"

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "错误：Ollama embeddings 返回 HTTP ${HTTP_STATUS}。响应：$HTTP_BODY" >&2
  exit 1
fi

VEC=$(echo "$HTTP_BODY" | python3 -c "
import sys, json
d = json.load(sys.stdin)
e = d.get('embedding') or []
if not e:
    print('错误：embeddings 响应无 embedding 字段', file=sys.stderr); sys.exit(1)
print(json.dumps(e))
") || { echo "错误：解析 embeddings 响应失败。响应：$HTTP_BODY" >&2; exit 1; }

if [[ "$NO_CACHE" != "1" && -n "$CACHE_FILE" ]]; then
  mkdir -p "$CACHE_DIR" 2>/dev/null || true
  printf '%s\n' "$VEC" > "$CACHE_FILE" 2>/dev/null || true
fi

printf '%s\n' "$VEC"
