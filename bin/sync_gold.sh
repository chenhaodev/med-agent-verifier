#!/usr/bin/env bash
# sync_gold.sh — 把兄弟项目的 Track B book gold 快照**vendored 进本仓**（data/book-gold/）。
#
# 动机：让本仓**自包含、可复现**——日常 eval 不再依赖 ../med-agent-* 在位。兄弟 gold 是会演进的
# 单一真相源，但评测应对**固定快照**跑（leaderboard 可复现），故采「vendored 快照 + 显式刷新」：
#   - load_dataset.py 默认读 data/book-gold/*.yaml（兄弟缺席也能跑）；
#   - 兄弟更新后，跑本脚本刷新快照并记录 provenance（来源路径/时间/兄弟 git commit/题数）。
# 注：`--track live`（eval_live.sh/run_sibling.sh）会**实时执行兄弟 Agent**，那条路仍需兄弟在位，
#     但那是可选特性，与核心静态 eval 解耦。
#
# 用法：
#   ./bin/sync_gold.sh                      # 从默认 ../med-agent-{internists,psy} 刷新
#   MED_AGENT_INTERNISTS=/path ./bin/sync_gold.sh   # 自定义兄弟路径（env）
#   ./bin/sync_gold.sh --internists /p1 --psy /p2   # 或用 flag

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DEST="$ROOT_DIR/data/book-gold"

INTERNISTS="${MED_AGENT_INTERNISTS:-$ROOT_DIR/../med-agent-internists}"
PSY="${MED_AGENT_PSY:-$ROOT_DIR/../med-agent-psy}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --internists) INTERNISTS="$2"; shift 2 ;;
    --psy)        PSY="$2";        shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

mkdir -p "$DEST"
SRC_MD="$DEST/SOURCE.md"
{
  echo "# data/book-gold/ — vendored 快照（勿手改；由 bin/sync_gold.sh 生成）"
  echo ""
  echo "Track B book gold 自兄弟项目快照而来。刷新：\`./bin/sync_gold.sh\`。"
  echo ""
  echo "| name | 源路径 | 同步时间 | 兄弟 commit | 题数 |"
  echo "|------|--------|----------|-------------|------|"
} > "$SRC_MD"

count_q() {  # 数 gold.yaml 里 questions: 列表长度（纯 python，避免依赖 yq）
  python3 - "$1" <<'PYEOF'
import sys, yaml
try:
    d = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
    print(len(d.get("questions", []) or []))
except Exception:
    print("?")
PYEOF
}

sync_one() {
  local name="$1" repo="$2"
  local src="$repo/eval/gold.yaml"
  if [[ ! -f "$src" ]]; then
    echo "⚠ 跳过 $name：未找到 $src（兄弟不在位）" >&2
    echo "| $name | （缺失，未同步） | - | - | - |" >> "$SRC_MD"
    return 0
  fi
  cp "$src" "$DEST/$name.yaml"
  local commit="-"
  if git -C "$repo" rev-parse --short HEAD >/dev/null 2>&1; then
    commit="$(git -C "$repo" rev-parse --short HEAD)"
  fi
  local n; n="$(count_q "$DEST/$name.yaml")"
  echo "✓ $name ← $src  ($n 题, commit $commit)"
  echo "| $name | \`$src\` | $(date '+%Y-%m-%d %H:%M') | $commit | $n |" >> "$SRC_MD"
}

sync_one internists "$INTERNISTS"
sync_one psy "$PSY"
echo ""
echo "已写入：$DEST/{internists,psy}.yaml + SOURCE.md"
