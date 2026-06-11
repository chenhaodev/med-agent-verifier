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
           for p in glob.glob(os.path.join(root, "data", "medbench-agent-95", "*.jsonl"))}
missing = on_disk - registered
if missing:
    print(f"  registry 未登记：{sorted(missing)}", file=sys.stderr)
    sys.exit(1)
PYEOF

# 2) 两路 gold round-trip
A=$(python3 "$SCRIPT_DIR/load_dataset.py" --track medbench --count 2>/dev/null || echo 0)
B=$(python3 "$SCRIPT_DIR/load_dataset.py" --track book --count 2>/dev/null || echo 0)
[[ "$A" -gt 0 ]] && pass "Track A 加载 $A 条" || fail "Track A 加载为 0"
[[ "$B" -gt 0 ]] && pass "Track B 加载 $B 条（vendored 快照）" || fail "Track B 加载为 0（data/book-gold/ 快照缺失？跑 ./bin/sync_gold.sh）"

# 3) judge 资产存在（含 TASK2 新增的 probe/TIA/freshness rubric）
for f in eval/judge_prompt.md eval/judge_prompt_reference.md eval/judge_prompt_probe.md \
         eval/judge_prompt_tia.md eval/judge_prompt_freshness.md eval/judge_prompt_hallu.md \
         eval/calibration/hallu_gold.yaml eval/METRICS.md \
         bin/parse_judge.py bin/parse_hallu.py; do
  [[ -s "$ROOT_DIR/$f" ]] && pass "$f 存在" || fail "$f 缺失"
done

# 4) E2E 冒烟（候选侧，零 judge 预算）
if "$SCRIPT_DIR/smoke.sh" >/tmp/verifier_smoke.log 2>&1; then
  pass "smoke 通过"
else
  fail "smoke 失败（见 /tmp/verifier_smoke.log）"
fi

# 5) TASK2 特性脚本健全性（零 judge 预算：仅语法/解析/loader）
for s in leaderboard build_routing gen_probes gen_tool_decision eval_routing parse_choice \
         freshness_audit parse_hallu specialty_map specialty_report calibrate_hallu model_pool; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$ROOT_DIR/bin/$s.py" 2>/dev/null \
    && pass "bin/$s.py 语法 ok" || fail "bin/$s.py 语法错误"
done
for s in leaderboard run_sibling eval_live freshness_audit sync_gold \
         eval eval_worker call_judge call_ollama call_openai_compat call_candidate \
         run_candidate run_pool smoke; do
  bash -n "$ROOT_DIR/bin/$s.sh" 2>/dev/null && pass "bin/$s.sh 语法 ok" || fail "bin/$s.sh 语法错误"
done
# 模型池 schema 校验 + 至少 1 个 enabled（loader 内含 backend/think/重复校验）
python3 - "$SCRIPT_DIR" <<'PYEOF' && pass "model_pool.yaml schema ok（含 enabled 模型）" || fail "model_pool.yaml schema 校验失败"
import sys
sys.path.insert(0, sys.argv[1])
import model_pool
assert any(e["enabled"] for e in model_pool.load_pool()), "池内无 enabled 模型"
PYEOF
# leaderboard 须能在零/部分数据下解析不崩
python3 "$SCRIPT_DIR/leaderboard.py" >/dev/null 2>&1 \
  && pass "leaderboard.py 聚合（含零/部分数据）" || fail "leaderboard.py 聚合失败"
# 专科覆盖盘点须能跑通（顺带验证兄弟 gold 经 load_dataset 可读）
python3 "$SCRIPT_DIR/specialty_report.py" >/dev/null 2>&1 \
  && pass "specialty_report.py 专科盘点 ok" || fail "specialty_report.py 失败"
# 幻觉判官标定集 schema + calibrate metrics 离线自检（零 judge 预算）
python3 - "$ROOT_DIR" <<'PYEOF' && pass "hallu_gold schema + calibrate metrics ok" || fail "hallu 标定自检失败"
import os, sys, yaml
root = sys.argv[1]
items = yaml.safe_load(open(os.path.join(root, "eval", "calibration", "hallu_gold.yaml")))["items"]
assert items and all({"id", "claim", "gold"} <= set(i) for i in items), "缺字段"
assert all(i["gold"] in ("supported", "unsupported", "not_sure") for i in items), "非法 gold"
sys.path.insert(0, os.path.join(root, "bin"))
import calibrate_hallu as c
m = c.metrics([{"id": "x", "tier": "easy", "gold": "unsupported", "pred": "unsupported"},
               {"id": "y", "tier": "easy", "gold": "supported", "pred": "supported"}])
assert m["unsupported_detection"]["tp"] == 1 and m["accuracy_3class"] == 1.0
PYEOF
# 探针/工具决策 loader round-trip（确定性，无 API）
PB=$(python3 "$SCRIPT_DIR/load_dataset.py" --track probe --count 2>/dev/null || echo -1)
TD=$(python3 "$SCRIPT_DIR/load_dataset.py" --track tool_decision --count 2>/dev/null || echo -1)
[[ "$PB" -ge 0 ]] && pass "probe loader ok（$PB 条 verified）" || fail "probe loader 失败"
[[ "$TD" -ge 0 ]] && pass "tool_decision loader ok（$TD 条）" || fail "tool_decision loader 失败"

# 6) 单元测试套件（stdlib unittest，零外部依赖、零 judge 预算）
if [[ -d "$ROOT_DIR/tests" ]]; then
  if T_OUT=$(cd "$ROOT_DIR" && python3 -m unittest discover -s tests 2>&1); then
    N=$(printf '%s' "$T_OUT" | grep -oE 'Ran [0-9]+' | grep -oE '[0-9]+' || echo "?")
    pass "单元测试通过（$N 用例）"
  else
    printf '%s\n' "$T_OUT" | tail -5 >&2
    fail "单元测试失败"
  fi
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━"
[[ $RC -eq 0 ]] && echo "✓ 全部门禁通过，可运行 eval.sh" || echo "✗ 存在未通过门禁" >&2
exit $RC
