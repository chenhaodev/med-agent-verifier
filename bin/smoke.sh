#!/usr/bin/env bash
# smoke.sh — E2E 冒烟：每路 gold 各取一条，跑通「候选作答」一段（不触 judge API）。
# 验证 Ollama 可达 + 两路 loader round-trip + run_candidate 能产文本。零 DeepSeek 预算。
# 退出码：全部成功 0；任一失败非 0。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() { echo "✗ $1" >&2; exit 1; }

echo "── smoke: Track A (medbench) ──"
REC_A=$(python3 "$SCRIPT_DIR/load_dataset.py" --track medbench --task MedShield --limit 1) \
  || fail "load_dataset medbench 失败"
[[ -n "$REC_A" ]] || fail "medbench 取不到记录"
Q_A=$(printf '%s' "$REC_A" | python3 -c "import json,sys;print(json.loads(sys.stdin.read())['question'][:200])")
RESP_A=$(printf '%s' "$Q_A" | "$SCRIPT_DIR/run_candidate.sh") || fail "Track A 候选作答失败（Ollama 未运行？）"
[[ -n "${RESP_A// /}" ]] || fail "Track A 候选回答为空"
echo "✓ Track A 候选作答 ${#RESP_A} 字"

echo "── smoke: Track B (book) ──"
REC_B=$(python3 "$SCRIPT_DIR/load_dataset.py" --track book --domain cardiology --limit 1) \
  || fail "load_dataset book 失败"
[[ -n "$REC_B" ]] || fail "book 取不到记录"
Q_B=$(printf '%s' "$REC_B" | python3 -c "import json,sys;print(json.loads(sys.stdin.read())['question'])")
RESP_B=$(printf '%s' "$Q_B" | "$SCRIPT_DIR/run_candidate.sh") || fail "Track B 候选作答失败"
[[ -n "${RESP_B// /}" ]] || fail "Track B 候选回答为空"
echo "✓ Track B 候选作答 ${#RESP_B} 字"

echo "✓ smoke 通过（两路 loader + Ollama 候选作答均正常）"
