#!/usr/bin/env bash
# leaderboard.sh — 跨模型排行榜（leaderboard.py 的薄封装，对齐其它 bin/*.sh 包装 .py 的惯例）。
# 用法：
#   ./bin/leaderboard.sh            # 写 eval/leaderboard.json
#   ./bin/leaderboard.sh --md       # 同时打印并写 eval/leaderboard.md
#   ./bin/leaderboard.sh --common   # 每桶仅取所有受比模型共有的 record-id（严格可比）
#   ./bin/leaderboard.sh --since 2026-06-27  # 只聚合该日期起的结果（挡掉陈旧跑渗入新报告）
# 透传所有参数给 leaderboard.py。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/leaderboard.py" "$@"
