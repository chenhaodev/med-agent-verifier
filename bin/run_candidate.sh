#!/usr/bin/env bash
# run_candidate.sh — 候选模型作答（Phase 1：raw question only）
# 用法：printf '%s' '<question 文本>' | ./bin/run_candidate.sh \
#         [--backend ollama|openai|siliconflow|litellm] [--model M] [--no-cache]
#
# 设计（已决策）：Track B 不注入 patient/doctor 模式框架，直接送原始问题，
# 以测试「无脚手架」的本地模型医学能力。后续 phase 可在此处挂可选系统提示。
# 当前为薄封装，把问题原样交给 call_candidate.sh（后端调度见该脚本头注）。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cat | "$SCRIPT_DIR/call_candidate.sh" "$@"
