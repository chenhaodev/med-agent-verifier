#!/usr/bin/env bash
# run_pool.sh — 按 eval/model_pool.yaml 逐模型跑评测（排行榜的「整池开考」入口）。
#
# 用法：
#   ./bin/run_pool.sh                          # 池内全部 enabled 模型 × --subset medium
#   ./bin/run_pool.sh --subset mini            # 换子集
#   ./bin/run_pool.sh --track book --sample 3  # 任意 eval.sh 参数原样透传
#   ./bin/run_pool.sh --from-shortlist eval/theory/shortlist.yaml  # 只跑阶段1理论短名单选中的模型
#   ./bin/run_pool.sh --orchestration          # 每模型额外跑 probe + tool_decision + routing（族③补全）
#   ./bin/run_pool.sh --dry-run                # 只打印将执行的命令，不跑
#
# 设计：
#   · 池（谁参赛）与 eval.sh（怎么考）解耦：本脚本只读池、组命令、循环；
#     除 --orchestration/--from-shortlist/--dry-run 外所有参数**原样透传** eval.sh（--model/
#     --backend/--think 由参赛条目提供，不可透传覆盖——要单跑某模型请直接用 eval.sh）。
#   · 单模型失败不中断整池（记录后继续），结尾汇总成败并提示 leaderboard。
#   · 默认 --subset medium：对齐「per-domain 路由需要 medium 起步」的部署门槛（CLAUDE.md）。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
ORCHESTRATION=0
SHORTLIST=""   # 阶段1 理论短名单：只跑 tier∈{must-test,optional} 且 testable 的模型
PASS_ARGS=()
HAS_SCOPE=0   # 用户是否已自带 --subset/--track（没有才注入默认 --subset medium）

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)        DRY_RUN=1;       shift ;;
    --orchestration)  ORCHESTRATION=1; shift ;;
    --from-shortlist) SHORTLIST="$2";  shift 2 ;;
    --model|--backend|--think)
      echo "错误：$1 由池条目（eval/model_pool.yaml）决定；单跑某模型请直接用 eval.sh。" >&2
      exit 1 ;;
    --subset|--track) HAS_SCOPE=1; PASS_ARGS+=("$1" "$2"); shift 2 ;;
    *) PASS_ARGS+=("$1"); shift ;;
  esac
done
[[ "$HAS_SCOPE" == "1" ]] || PASS_ARGS=(--subset medium ${PASS_ARGS[@]+"${PASS_ARGS[@]}"})

POOL_TSV=$(python3 "$SCRIPT_DIR/model_pool.py" --tsv) || {
  echo "错误：模型池加载失败（eval/model_pool.yaml schema？）。" >&2
  exit 1
}
[[ -n "$POOL_TSV" ]] || { echo "错误：池内没有 enabled 模型。" >&2; exit 1; }

# 阶段1→阶段2 接缝：按短名单过滤池（理论想测但本地缺 → 报 missing_local，不中断）。
if [[ -n "$SHORTLIST" ]]; then
  [[ -f "$SHORTLIST" ]] || { echo "错误：短名单不存在：$SHORTLIST" >&2; exit 1; }
  POOL_TSV=$(printf '%s\n' "$POOL_TSV" \
    | python3 "$SCRIPT_DIR/filter_shortlist.py" "$SHORTLIST") || {
      echo "错误：短名单过滤失败：$SHORTLIST" >&2; exit 1; }
  [[ -n "$POOL_TSV" ]] || { echo "错误：短名单与本地池无交集（全 missing_local？）。" >&2; exit 1; }
fi

run() {  # 打印并（非 dry-run 时）执行；失败返回非 0 但不退出整池
  echo "→ $*"
  [[ "$DRY_RUN" == "1" ]] && return 0
  "$@"
}

N_TOTAL=0; N_FAIL=0; FAILED=()
while IFS=$'\t' read -r NAME BACKEND THINK; do
  N_TOTAL=$((N_TOTAL + 1))
  echo ""
  echo "━━━ 池 [$N_TOTAL] $NAME (backend=$BACKEND${THINK:+ think=$THINK}) ━━━"
  THINK_ARGS=(); [[ -n "$THINK" ]] && THINK_ARGS=(--think "$THINK")

  OK=1
  run "$SCRIPT_DIR/eval.sh" --model "$NAME" --backend "$BACKEND" \
      ${THINK_ARGS[@]+"${THINK_ARGS[@]}"} ${PASS_ARGS[@]+"${PASS_ARGS[@]}"} || OK=0
  if [[ "$ORCHESTRATION" == "1" ]]; then
    run "$SCRIPT_DIR/eval.sh" --track probe --model "$NAME" --backend "$BACKEND" \
        ${THINK_ARGS[@]+"${THINK_ARGS[@]}"} || OK=0
    run "$SCRIPT_DIR/eval.sh" --track tool_decision --model "$NAME" --backend "$BACKEND" \
        ${THINK_ARGS[@]+"${THINK_ARGS[@]}"} || OK=0
    run python3 "$SCRIPT_DIR/eval_routing.py" --model "$NAME" --backend "$BACKEND" \
        ${THINK_ARGS[@]+"${THINK_ARGS[@]}"} || OK=0
  fi
  if [[ "$OK" == "0" ]]; then
    N_FAIL=$((N_FAIL + 1)); FAILED+=("$NAME")
    echo "✗ $NAME 有失败步骤（继续下一个模型）"
  fi
done <<< "$POOL_TSV"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " 整池完成：$((N_TOTAL - N_FAIL))/$N_TOTAL 模型全部步骤成功"
[[ "$N_FAIL" -gt 0 ]] && printf '   失败：%s\n' "${FAILED[@]}"
echo " 下一步：./bin/leaderboard.sh --md && python3 bin/report_scenario.py（出场景报告）"
[[ "$N_FAIL" -eq 0 ]]
