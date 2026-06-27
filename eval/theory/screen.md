# 理论评定 · medical × agent （2026-06-27）

> 纸面 triage，**非实测分**。证据=../agent-bench/entries/*.md frontmatter。
> 权重 {'w_rank': 0.4, 'w_auth': 0.3, 'w_verdict': 0.3, 'p_contam': 0.2}。闭源冠军→天花板参照；本地模型→可实测候选。

## 可实测候选（交阶段2 run_pool --from-shortlist）

| 模型 | tier | theory_score | license | 依据 |
|---|---|---|---|---|
| qwen3.5:latest | optional | 0.4 | open-weights | 无同族榜单证据：agent-bench 该场景冠军无 qwen 本地近邻 → 中性先验 0.4，区分留给阶段2实测 |
| qwen3.5:2b | optional | 0.4 | open-weights | 无同族榜单证据：agent-bench 该场景冠军无 qwen 本地近邻 → 中性先验 0.4，区分留给阶段2实测 |
| qwen2.5:1.5b | skip | 0.4 | open-weights | 无同族榜单证据：agent-bench 该场景冠军无 qwen 本地近邻 → 中性先验 0.4，区分留给阶段2实测 |
| qwen3.5:0.8b | skip | 0.4 | open-weights | 无同族榜单证据：agent-bench 该场景冠军无 qwen 本地近邻 → 中性先验 0.4，区分留给阶段2实测 |

## 天花板参照（闭源，testable=false，不入实测队列）

| 标杆 | theory_score | 出处 |
|---|---|---|
| Claude Sonnet 4.5 | 0.884 | medbench |
| GPT-5 | 0.884 | medbench |
| Claude Sonnet 4.5 (agent) | 0.884 | medbench |
| PULSE | 0.562 | pulse-ecg |
| GPT-5.2 | 0.552 | rcq |
| Gemini 3.1 Pro | 0.552 | rcq |
| Claude Opus 4.6 | 0.552 | rcq |
| OpenEvidence | 0.552 | rcq |
| UpToDate Expert AI | 0.552 | rcq |
