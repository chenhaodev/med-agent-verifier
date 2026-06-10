#!/usr/bin/env bash
# run_sibling.sh — 取兄弟「严肃 Agent」对一个问题的**现答**作为动态 gold（Workstream C）。
# 用法：printf '%s' '<问题>' | ./bin/run_sibling.sh --agent internists|psy [--mode patient|doctor] [--no-cache]
# 输出：兄弟 ask.sh 的回答纯文本（去掉 ═══ 装饰线，trim 首尾空白）。
#
# 缓存：按 {agent, mode, 问题} 的 sha256 内容寻址磁盘缓存（.cache/sibling/<sha>.txt），
#   绕过：NO_CACHE=1 或 --no-cache。兄弟自身依赖 DeepSeek（评测期成本，可接受），缓存→零重复花费。
# 说明：兄弟知识为静态（书本+2024 指南），本路测「与书本 Agent 的一致性」，非绝对真值——与 D 配合看时效。

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

AGENT="internists"
MODE="patient"
NO_CACHE="${NO_CACHE:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)    AGENT="$2";  shift 2 ;;
    --mode)     MODE="$2";   shift 2 ;;
    --no-cache) NO_CACHE=1;  shift ;;
    *) echo "未知参数：$1" >&2; exit 1 ;;
  esac
done

# 兄弟路径可经 MED_AGENT_* env 覆盖（与 sync_gold.sh 一致），默认 ../med-agent-*。
# 注：`--track live` 是**可选**特性——它实时执行兄弟 Agent，故必须兄弟在位；
# 核心静态 eval 已自包含（读 data/book-gold/ vendored 快照，不需要兄弟）。
case "$AGENT" in
  internists) SIB_DIR="${MED_AGENT_INTERNISTS:-$ROOT_DIR/../med-agent-internists}" ;;
  psy)        SIB_DIR="${MED_AGENT_PSY:-$ROOT_DIR/../med-agent-psy}" ;;
  *) echo "错误：--agent 须为 internists|psy（收到：$AGENT）" >&2; exit 1 ;;
esac
ASK="$SIB_DIR/bin/ask.sh"
[[ -x "$ASK" ]] || { echo "错误：兄弟 ask.sh 不存在或不可执行：$ASK（live 是可选特性，需兄弟在位）" >&2; exit 1; }

QUESTION="$(cat)"
[[ -n "${QUESTION// /}" ]] || { echo "错误：空问题。" >&2; exit 1; }

CACHE_DIR="$ROOT_DIR/.cache/sibling"
KEY=$(printf '%s\n%s\n%s' "$AGENT" "$MODE" "$QUESTION" | shasum -a 256 | awk '{print $1}')
CACHE_FILE="$CACHE_DIR/$KEY.txt"
if [[ "$NO_CACHE" != "1" && -s "$CACHE_FILE" ]]; then
  cat "$CACHE_FILE"
  exit 0
fi

# 调兄弟 Agent（debug 走 stderr，答案走 stdout）；去 ═══ 装饰线并 trim 首尾空行。
RAW=$("$ASK" "$QUESTION" --mode "$MODE" 2>/dev/null) || {
  echo "错误：兄弟 ask.sh 调用失败（$AGENT）。" >&2
  exit 1
}
CLEAN=$(printf '%s' "$RAW" | python3 -c '
import sys, re
lines = sys.stdin.read().splitlines()
lines = [ln for ln in lines if not re.fullmatch(r"\s*[═=]{3,}\s*", ln or "")]
text = "\n".join(lines).strip("\n").strip()
print(text)
')
[[ -n "${CLEAN// /}" ]] || { echo "错误：兄弟回答为空（去装饰后）。" >&2; exit 1; }

mkdir -p "$CACHE_DIR"
printf '%s' "$CLEAN" > "$CACHE_FILE"
printf '%s' "$CLEAN"
