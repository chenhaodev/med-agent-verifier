# Makefile — med-agent-verifier 任务入口（把分散在 CLAUDE.md/README 的命令收敛成可发现的 target）。
# 约定：bash 编排 + Python 处理数据；判官=DeepSeek，候选=本地 Ollama（默认）或 OpenAI-compatible 端点
#（--backend openai|siliconflow|litellm，经 bin/call_candidate.sh 调度）。详见 README.md / eval/METRICS.md。

# 可覆盖变量：make eval MODEL=qwen3.5 TRACK=book SUBSET=mini HALLU=1 THINK=off
MODEL  ?= qwen3.5
TRACK  ?= both
SUBSET ?=
HALLU  ?=
THINK  ?=
JUDGE  ?=

PY := python3
_SUBSET = $(if $(SUBSET),--subset $(SUBSET),)
_HALLU  = $(if $(HALLU),--hallu,)
_THINK  = $(if $(THINK),--think $(THINK),)
_JUDGE  = $(if $(JUDGE),--judge-model $(JUDGE),)

.DEFAULT_GOAL := help
.PHONY: help install sync check test lint eval leaderboard theory calibrate specialty probes clean

help:  ## 列出所有 target
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## 安装依赖（仅 pyyaml）
	pip install -r requirements.txt

sync:  ## vendor Track B book gold ← 兄弟项目 → data/book-gold/（可改 MED_AGENT_* env）
	./bin/sync_gold.sh

check:  ## 静态门禁（registry/gold/smoke/语法/标定/单元测试；零判官预算）
	./bin/check.sh

test:  ## 单元测试套件（stdlib unittest，零依赖）
	$(PY) -m unittest discover -s tests -v

lint:  ## ruff（line-length 99, E/F/I）
	ruff check bin/*.py tests/

eval:  ## 跑评测：make eval MODEL=.. [TRACK=both|book|medbench] [SUBSET=mini] [HALLU=1] [THINK=off]
	./bin/eval.sh --track $(TRACK) $(_SUBSET) $(_HALLU) $(_THINK) $(_JUDGE) --model $(MODEL)

pool:  ## 整池开考：model_pool.yaml 全部 enabled 模型 × medium（POOL_ARGS='--dry-run' 等透传）
	./bin/run_pool.sh $(POOL_ARGS)

pool-list:  ## 看模型池名单（含 disabled）
	$(PY) bin/model_pool.py --list --all

leaderboard:  ## 聚合所有结果 → 三族分轴排行榜（Markdown）
	./bin/leaderboard.sh --md

theory:  ## 阶段1 理论评定：读 agent-bench 证据 → eval/theory/shortlist.yaml（零 LLM）
	$(PY) bin/theory_screen.py --domain medical --axis agent --out eval/theory/shortlist.yaml

calibrate:  ## 标定幻觉判官检测准确度（MedHallu 式 P/R/F1，含 hard 层）→ eval/METRICS.md
	$(PY) bin/calibrate_hallu.py

specialty:  ## Track B 专科覆盖盘点（judge-free，零预算）
	$(PY) bin/specialty_report.py

probes:  ## （重）生成冻结探针集 + 工具决策集
	$(PY) bin/gen_probes.py && $(PY) bin/gen_tool_decision.py

clean:  ## 清理派生产物（结果/缓存/排行榜/场景报告；不动 gold 与探针）
	rm -rf eval/results/*.json eval/results/*_summary.txt .cache \
	  eval/leaderboard.json eval/leaderboard.md eval/reports/*.md \
	  eval/calibration/last_report.json eval/freshness 2>/dev/null || true
	@echo "已清理派生产物。"
