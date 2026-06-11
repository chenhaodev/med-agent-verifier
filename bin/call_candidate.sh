#!/usr/bin/env bash
# call_candidate.sh — 候选模型统一调度器（candidate backend dispatcher）
# 用法：printf '%s' '<prompt>' | ./bin/call_candidate.sh \
#         [--backend ollama|openai|siliconflow|litellm] [--model M] [--think on|off] [--no-cache]
#
# 把「被测候选是谁」与「评测流程怎么跑」解耦：上游（run_candidate/eval_worker/eval_routing）
# 只面向本脚本；新增后端 = 加一个 call_*.sh + 在 case 里登记一行。
#
# 后端协议两类：
#   ollama                → 官方 Ollama REST /api/generate（call_ollama.sh）
#   openai|siliconflow|litellm → OpenAI-compatible /v1/chat/completions（call_openai_compat.sh），
#                            仅 base_url + api key 来源不同（预设见下）：
#     openai       OPENAI_BASE_URL（默认 https://api.openai.com/v1）        + OPENAI_API_KEY
#     siliconflow  SILICONFLOW_BASE_URL（默认 https://api.siliconflow.cn/v1）+ SILICONFLOW_API_KEY
#     litellm      LITELLM_BASE_URL（默认 http://localhost:4000/v1）         + LITELLM_API_KEY（可空）
#
# 预设以显式 flag 下传（而非 env：下游会 source .env，env 继承会被覆盖——flag 在 source 后解析，必胜）。
# 选择后端：--backend 或 .env CANDIDATE_BACKEND（默认 ollama，零配置时行为与旧版完全一致）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"

BACKEND="${CANDIDATE_BACKEND:-ollama}"

# 抽出 --backend，其余参数原样透传给具体后端
PASS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend) BACKEND="$2"; shift 2 ;;
    *) PASS_ARGS+=("$1"); shift ;;
  esac
done

case "$BACKEND" in
  ollama)
    exec "$SCRIPT_DIR/call_ollama.sh" ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
    ;;
  openai)
    exec "$SCRIPT_DIR/call_openai_compat.sh" \
      --base-url "${OPENAI_BASE_URL:-https://api.openai.com/v1}" \
      --api-key-env OPENAI_API_KEY \
      ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
    ;;
  siliconflow)
    exec "$SCRIPT_DIR/call_openai_compat.sh" \
      --base-url "${SILICONFLOW_BASE_URL:-https://api.siliconflow.cn/v1}" \
      --api-key-env SILICONFLOW_API_KEY \
      ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
    ;;
  litellm)
    exec "$SCRIPT_DIR/call_openai_compat.sh" \
      --base-url "${LITELLM_BASE_URL:-http://localhost:4000/v1}" \
      --api-key-env LITELLM_API_KEY \
      ${PASS_ARGS[@]+"${PASS_ARGS[@]}"}
    ;;
  *)
    echo "错误：未知候选后端：${BACKEND}（支持 ollama|openai|siliconflow|litellm）" >&2
    exit 1
    ;;
esac
