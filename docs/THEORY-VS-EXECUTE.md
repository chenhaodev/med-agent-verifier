# 理论 vs 执行：为什么 verifier 分两阶段

> 本文解释 med-agent-verifier 的终产物为何从「离线 MoA 路由清单」改成「按场景实测报告」，
> 以及两阶段（理论评定 → 按场景执行）各自的边界与已知脆弱性。
> 配套代码：`bin/theory_screen.py`（阶段1）、`bin/run_pool.sh --from-shortlist` +
> `bin/report_scenario.py`（阶段2）。权重/公式定义见 `eval/METRICS.md` §6。

## 0. 一句话

```
旧：model_pool.yaml（全量盲跑） → run_pool → leaderboard → 派生一张通用 MoA 路由清单（已退役）
新：agent-bench 证据 → [阶段1 理论评定] shortlist.yaml → [阶段2 按场景 execute] → eval/reports/<场景>.md
```

终产物不再是一张「谁该干哪活」的**通用** MoA 路由清单，而是一份**针对具体场景**（domain × axis）的
实测报告：候选由前置的 agent-bench 理论评级筛出，结论**仅就该场景成立**。

## 1. 为什么两阶段必须分开

**纸面证据会污染、会过期，且只覆盖闭源标杆。** agent-bench 收录的是公开榜单的权威评级——它能告诉你
「在 medbench/healthbench 上谁强」，但有三重不可直接采信的理由：

1. **污染**：公开榜题目可能进了预训练语料（在线榜尤甚）。`theory_score` 对
   `genre=online-leaderboard` 且无 `contamination_controls` 的条目扣分，但扣分≠免疫。
2. **过期**：榜单冠军随模型迭代翻新；frontmatter 的 `as_of` / `freshness` 只是快照。
3. **闭源标杆 ≠ 可实测候选**（最关键）：医疗·agent 轴上 `models_ranked` 的冠军**几乎全是闭源前沿
   模型**（Claude / GPT / Gemini），而 verifier 实测的是**本地 / OpenAI-compatible** 候选。纸面第一名
   你根本跑不到，也不该塞进实测队列。

因此：**理论层只读 agent-bench、只做 triage（先验）；执行层只测短名单、给出真正可信的分。**
两层职责不可混——这正是 §3 的两条硬边界。

## 2. 阶段1 · 理论评定（`theory_screen.py`，纯纸面、零 LLM）

输入 `../agent-bench/entries/*.md` 的 frontmatter，按场景（`--domain` / `--axis`）给每个候选合成
`theory_score`（公式见 METRICS.md §6），输出 `eval/theory/shortlist.yaml` + 人读 `screen.md`。

两类输出，显式区分（这是「理论 vs 执行」的接缝）：

| 类别 | testable | 角色 | 例 |
|------|----------|------|----|
| **天花板参照** `ceiling_refs` | `false` | 闭源冠军，作上界参照，**不入实测队列** | Claude Sonnet 4.5 (agent)、GPT-5、Gemini 3.1 Pro |
| **可实测候选** `candidates` | `true` | `model_pool.yaml` 里的本地模型，进阶段2 | qwen3.5:latest / :2b / :0.8b、qwen2.5:1.5b |

**闭源 → 可实测的映射规则（启发式同族）**：把闭源冠军的证据迁移到本地候选的先验，规则是
**模型名家族词（claude/gpt/qwen/llama/glm/baichuan/…）+ 规模数字（如 32B/7b）∩ `model_pool.yaml`**：
- 命中同族 → 用该标杆的 `base_score` × 场景契合度 × 规模接近度作先验，`why` 注明迁移链路。
- **无同族证据**（医疗·agent 轴的常态，因冠军全闭源）→ 退回**中性先验 0.4**，`why` 明写
  「无同族榜单证据，区分留给阶段2实测」。**绝不编造区分度。**

### ⚠ 已知脆弱性（必须人核对）

启发式同族映射是两阶段里**最脆的一环**，刻意保持可解释（每条 `why` 回链真实字段）以便人工复核：

- **字符串匹配易误**：家族词/规模靠正则从模型名抽取。`qwen3.5:latest` 抽不出规模（→ `size_b: null`）；
  改名/别名/量化后缀（`:Q2_K`）可能漏判同族。
- **跨轴迁移降权但仍是近似**：同族冠军若出现在非目标领域（如 bfcl 的 agent 轴而非医疗），证据按
  `relevance` 降权迁移——这是「有总比没有强」的近似，不是严格同分布证据。
- **中性先验抹平区分**：医疗场景下本地候选常全部并列 0.4。**这是诚实结论**（榜单确实没排本地模型），
  不是 bug——它把判别压力如实交给阶段2实测，而非假装纸面能分高下。

结论：**theory_score 只用于决定「谁值得花预算实测」，永不用于宣称「谁更强」。** 后者只有阶段2能回答。

## 3. 阶段2 · 按场景执行（`run_pool --from-shortlist` → `report_scenario.py`）

- `run_pool.sh --from-shortlist eval/theory/shortlist.yaml` 只跑 `tier ∈ {must-test, optional}` 且
  `testable=true` 的候选（`bin/filter_shortlist.py` 做过滤）；理论想测但本地没拉取 → 报 `missing_local`，
  不中断。无该 flag 时退回读 `model_pool.yaml` 全量（向后兼容）。
- `model_pool.yaml` 角色**降级**为「本地可用模型清册」（谁已 `ollama pull`），不再是「考生名单」的
  唯一真相源——考生名单由 shortlist 决定。
- `report_scenario.py` 读 shortlist + `eval/leaderboard.json`，产出 `eval/reports/<domain>-<axis>-<date>.md`：
  **理论先验** 与 **实测三轴**（能力 Track A 0–40 / 诚信 Track B 0–40 + unsupported / 编排稳健 族③
  Accuracy）并列，标注**理论 vs 实测差异点**（纸面高、实测崩之类），给出**仅就本场景**的结论。

## 4. 两条硬边界（一句话记住）

1. **理论层只读 agent-bench**——不调模型、不碰实测结果，只产先验短名单。
2. **执行层只测短名单**——不再全池盲跑；实测分是唯一可信的「谁更强」依据，理论分仅是入场券。

## 5. 端到端

```bash
python3 bin/theory_screen.py --domain medical --axis agent --top 5 --out eval/theory/shortlist.yaml
./bin/run_pool.sh --from-shortlist eval/theory/shortlist.yaml --subset mini
./bin/leaderboard.sh --md
python3 bin/report_scenario.py --shortlist eval/theory/shortlist.yaml \
    --leaderboard eval/leaderboard.json --out eval/reports/
```

换场景（如 `--domain medical --axis safety-ethics`）需重跑阶段1+2——理论先验与结论都不跨场景复用。
