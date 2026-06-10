#!/usr/bin/env bash
# freshness_audit.sh — gold 时效性审计（Workstream D）的薄封装：source .env 后跑 freshness_audit.py。
# 用法：
#   ./bin/freshness_audit.sh --domain cardiology
#   ./bin/freshness_audit.sh --domain cardiology --max 8
# 只读：抽取某专科 gold 要点 → 判官判 current|drifted|uncertain → 写 eval/freshness/<domain>.md。
# 绝不修改 gold（兄弟项目是单一真相源）。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
[[ -f "$ROOT_DIR/.env" ]] && source "$ROOT_DIR/.env"
export DEEPSEEK_API_KEY DEEPSEEK_MODEL DEEPSEEK_TIMEOUT DEEPSEEK_MAX_RETRIES JUDGE_MODEL 2>/dev/null || true
exec python3 "$SCRIPT_DIR/freshness_audit.py" "$@"
