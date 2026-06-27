# 场景报告 · medical × agent（2026-06-27）

> 阶段1理论先验(agent-bench) + 阶段2按场景实测。**三轴互不可比**；
> 结论**仅就本场景**，不产出通用 MoA 路由清单。

**场景定义**：domain=`medical` · axis=`agent` · 理论评定 as_of `2026-06-27`。

## 理论短名单（先验，引 agent-bench）

| 模型 | tier | theory_score | 依据 |
|---|---|---|---|
| qwen3.5:latest | optional | 0.4 | 见 shortlist.yaml.why |
| qwen3.5:2b | optional | 0.4 | 见 shortlist.yaml.why |
| qwen2.5:1.5b | skip | 0.4 | 见 shortlist.yaml.why |
| qwen3.5:0.8b | skip | 0.4 | 见 shortlist.yaml.why |

### 天花板参照（闭源，testable=false，仅作上界）

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

## 实测三轴（按场景执行后）

| 模型 | 能力 Track A (0–40) | 诚信 Track B (0–40) | unsupported | 编排稳健 ③ (Acc) |
|---|---|---|---|---|
| qwen3.5:latest | 29.04 | 36.32 | 0% | 93% |
| qwen3.5:2b | 23.33 | 30.53 | 6% | 70% |
| qwen2.5:1.5b | n/a | n/a | n/a | n/a |
| qwen3.5:0.8b | n/a | n/a | n/a | n/a |

## 理论 vs 实测 差异点

- 理论序与实测序一致（本场景先验与实测同向）。

## 本场景结论

本场景实测综合最优：**qwen3.5:latest**（能力 29.04 / 诚信 36.32 / 编排 93%）。仅就此场景推荐，换场景需重跑阶段1+2。

