#!/usr/bin/env bash
# check.sh — 静态门禁（在 eval 消耗 judge 预算前必须全绿）。
#   1) registry 覆盖所有在仓的 MedBench task（jsonl 文件名）
#   2) 两路 gold 源均可加载并 round-trip（条数 > 0）
#   3) judge prompt 与解析器存在
#   4) E2E 冒烟（smoke.sh，候选侧）
# 退出码：全绿 0；任一红 1。判官 API 不在本门禁内（smoke 不触 judge）。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

RC=0
pass() { echo "✓ $1"; }
fail() { echo "✗ $1" >&2; RC=1; }

echo "━━━ check.sh 静态门禁 ━━━"

# 1) registry 覆盖 MedBench tasks
python3 - "$ROOT_DIR" <<'PYEOF' && pass "registry 覆盖全部 MedBench task" || fail "registry 缺 task 覆盖"
import glob, os, sys, yaml
root = sys.argv[1]
reg = yaml.safe_load(open(os.path.join(root, "eval", "task_registry.yaml")))
registered = set(reg.get("medbench", {}).get("tasks", {}).keys())
on_disk = {os.path.splitext(os.path.basename(p))[0]
           for p in glob.glob(os.path.join(root, "medbench-agent-95", "*.jsonl"))}
missing = on_disk - registered
if missing:
    print(f"  registry 未登记：{sorted(missing)}", file=sys.stderr)
    sys.exit(1)
PYEOF

# 2) 两路 gold round-trip
A=$(python3 "$SCRIPT_DIR/load_dataset.py" --track medbench --count 2>/dev/null || echo 0)
B=$(python3 "$SCRIPT_DIR/load_dataset.py" --track book --count 2>/dev/null || echo 0)
[[ "$A" -gt 0 ]] && pass "Track A 加载 $A 条" || fail "Track A 加载为 0"
[[ "$B" -gt 0 ]] && pass "Track B 加载 $B 条（兄弟 gold live）" || fail "Track B 加载为 0（兄弟项目路径？）"

# 3) judge 资产存在
for f in eval/judge_prompt.md eval/judge_prompt_reference.md bin/parse_judge.py; do
  [[ -s "$ROOT_DIR/$f" ]] && pass "$f 存在" || fail "$f 缺失"
done

# 4) E2E 冒烟（候选侧，零 judge 预算）
if "$SCRIPT_DIR/smoke.sh" >/tmp/verifier_smoke.log 2>&1; then
  pass "smoke 通过"
else
  fail "smoke 失败（见 /tmp/verifier_smoke.log）"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━"
[[ $RC -eq 0 ]] && echo "✓ 全部门禁通过，可运行 eval.sh" || echo "✗ 存在未通过门禁" >&2
exit $RC
