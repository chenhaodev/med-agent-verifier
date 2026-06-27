# PLAN · 简化 med-agent-verifier 范围(理论评定 → 按需 execute)

> 交给 ClaudeCode 在 `~/ClaudeCode/focus/med-agent-verifier` 内执行。
> 目标:**砍掉 MoA routing 选型清单**;把流程改成**两阶段**——
> ① 用 agent-bench 做"理论"评定 → 短名单;② 只对短名单按具体场景 execute 实测。
> 作者:chenhao · 日期:2026-06-27

---

## 0. 一句话目标

verifier 不再产出"离线 MoA 路由清单"。新的终产物是一份**针对具体场景的实测报告**,
其候选模型由**前置的 agent-bench 理论评级**筛出,而不是手维护的 `model_pool.yaml` 全量盲跑。

```
旧:model_pool.yaml(全量) → run_pool → leaderboard → build_routing → routing_manifest.yaml(MoA)
新:agent-bench 证据 → [阶段1 理论评定] → shortlist.yaml → [阶段2 按场景 execute] → report.md
```

---

## 1. 退役 MoA / routing(先做,降复杂度)

**目的**:移除"为离线 MoA 选型"的整层逻辑与文案,避免新流程继续被它牵引。

- **直接删除(已拍板,不归档)**——`git rm` 保留删除记录在 history 即可:
  - `bin/build_routing.py`
  - `bin/eval_routing.py`
  - `eval/routing_manifest.yaml`
  - `tests/` 中针对上述的用例(`grep -ril routing tests/`)
- `Makefile` / `bin/*.sh` 里指向 routing 的 target 与调用一并移除(`grep -rn routing bin Makefile`)。
- 文案清洗:`README.md`、`CLAUDE.md`、`eval/METRICS.md` 中所有 "MoA / routing_manifest / 路由清单 / 选型" 段落
  改写为新两阶段叙事(见 §4)。保留"两金标 / 能力×诚信双轴不混算"这一核心——它仍是地基。
- **保留**:`leaderboard.py` / `leaderboard.sh`(实测仍需排名),`load_dataset.py`,两金标 `data/`。

**验收**:`grep -rinE 'routing_manifest|build_routing|MoA 选型' --include=*.py --include=*.sh --include=*.md .` **零命中**(全仓彻底清除,不留 archive)。

---

## 2. 阶段 1 · 理论评定(新增,纯纸面、不调模型)

**输入**:`../agent-bench/entries/*.md`(frontmatter:`models_ranked` / `authority` / `genre` /
`methodology.contamination_controls` / `expert_verdict`)。
**产物**:`eval/theory/shortlist.yaml` + 人读的 `eval/theory/screen.md`。

新增 `bin/theory_screen.py`:

1. 解析 agent-bench 各 entry 的 frontmatter,抽出每个出现过的 `model`(含 `axis` / `rank` / `license` / `note`)。
2. 按场景过滤(CLI 参数,见下),对每个候选合成一个**理论分 `theory_score`**。
   **已拍板:纯启发式加权,零 LLM 调用、完全可解释、可复现**。建议公式(权重写进 `eval/METRICS.md`,便于调参):

   ```
   theory_score = w_rank·rank_score        # 相关 axis/domain 上名次的归一分(rank1=1.0 递减)
                + w_auth·authority_score    # 榜权威:institution_count / citation / 临床参与 → [0,1]
                + w_verdict·verdict_score   # expert_verdict.confidence: high=1 / med=0.6 / low=0.3
                - p_contam·contam_penalty   # genre=online-leaderboard 且无 contamination_controls → 扣分
   默认权重:w_rank=0.4, w_auth=0.3, w_verdict=0.3, p_contam=0.2(全部可被 CLI/配置覆盖)
   ```
   - 多个 entry 同时背书一个模型时,`authority_score`/`verdict_score` 取**加权最大**(最强背书源),`rank_score` 取相关 axis 上最好名次。
   - `license` 仅作标注/过滤(`--prefer-license` 时排序加成),**不进 `theory_score` 公式**。
   - 缺字段时按保守值代入(如无 `confidence` → 0.3),并在 `why` 里注明"字段缺失,保守估计"。
3. 输出 shortlist:`tier`(must-test / optional / skip)+ `why`(回链到具体 entry 与字段,**不许编造**)。

CLI(场景驱动):
```bash
python3 bin/theory_screen.py \
  --domain medical --axis agent \        # 场景:医疗·agent 能力
  --prefer-license open-weights \        # 可选:本地优先
  --top 5 \                              # 短名单上限
  --out eval/theory/shortlist.yaml
```

`shortlist.yaml` 形如:
```yaml
scenario: { domain: medical, axis: agent, as_of: 2026-06-27 }
candidates:
  - model: "Qwen3.x (open-weights 近邻)"   # 见 §2 备注:闭源榜冠军→映射到可本地实测的同族
    tier: must-test
    theory_score: 0.81
    why:
      - "medbench: agent axis rank1 由 closed 模型占据;authority=500 机构、有抗污染控制 → 榜可信"
      - "expert_verdict.confidence=high"
    source_entries: [medbench, healthbench]
```

> **关键映射备注**:agent-bench 的 `models_ranked` 多为**闭源**冠军(Claude/GPT),而 verifier 实测的是
> **本地/OpenAI-compatible** 候选。`theory_screen.py` 要把"榜上闭源标杆"转成"可实测的候选"两类输出:
> (a) **标杆 benchmark**(闭源,作为天花板参照,标 `testable=false`);
> (b) **可实测候选**(`model_pool.yaml` 里现有的本地模型),按其与标杆的同族/规模关系给理论先验。
> 这一步是"理论 vs 执行"的接缝,务必显式区分,别把闭源冠军直接塞进实测队列。

**验收**:`shortlist.yaml` 每条 `why` 都能在某个 agent-bench entry 的真实字段里找到出处;无悬空引用。

---

## 3. 阶段 2 · 按场景 execute(改造现有实测)

**目的**:实测不再盲跑全池,而是**只跑阶段 1 的 must-test(+可选 optional)**,且按场景选子集。

- 改 `bin/run_pool.sh`:新增 `--from-shortlist eval/theory/shortlist.yaml`,只对 `tier in (must-test[,optional])`
  且 `testable=true` 的模型执行;无该参数时退回读 `model_pool.yaml`(向后兼容)。
- `model_pool.yaml` 降级为"**本地可用模型清册**"(谁已 `ollama pull`),不再是"考生名单"的唯一真相源;
  考生名单由 shortlist 决定。两者由 `theory_screen.py` 的 (b) 类做交集校验(理论想测但本地没有 → 报 `missing_local`)。
- 实测产物:沿用 `eval/results/` + `leaderboard.{json,md}`,新增一份**场景报告** `eval/reports/<scenario>-<date>.md`,
  含:场景定义 → 理论短名单(引 agent-bench)→ 实测排名(能力轴/诚信轴**分列**)→ 理论 vs 实测差异点
  (例:某模型纸面高、实测诚信卷崩)→ 结论(此场景选谁,**仅就本场景,不产出通用 MoA 清单**)。

命令链(目标形态):
```bash
# 1) 理论筛
python3 bin/theory_screen.py --domain medical --axis agent --top 5 --out eval/theory/shortlist.yaml
# 2) 按短名单执行(只跑该测的)
./bin/run_pool.sh --from-shortlist eval/theory/shortlist.yaml --subset mini
./bin/leaderboard.sh
# 3) 出场景报告
python3 bin/report_scenario.py --shortlist eval/theory/shortlist.yaml --results eval/results --out eval/reports/
```

**验收**:`eval/reports/<scenario>-<date>.md` 同时含"理论先验"和"实测结果"两栏,且二者对得上候选集合;
跑一个 `--subset mini` 的小场景端到端通过。

---

## 4. 文档与契约更新

- `README.md` 顶部"一句话/范围":删 MoA 选型,改为"**先理论(agent-bench)后执行(按场景)的本地医学模型评测器**"。
- `CLAUDE.md`:管线图改为 §0 的"新"链;明确**理论层只读 agent-bench、执行层只测短名单**两条边界。
- 新增 `docs/THEORY-VS-EXECUTE.md`:讲清两阶段为何分开(纸面证据会污染/会过期 → 必须落到具体场景实测),
  以及"闭源标杆 ≠ 可实测候选"的映射规则(§2 备注)。
- `eval/METRICS.md`:新增 `theory_score` 的成分与权重定义;声明它是**先验/triage**,不替代实测分。

---

## 5. 执行顺序(给 ClaudeCode 的推荐 sprint)

1. **P0 退役 routing**(§1)→ 跑现有测试确认实测主链未断(`./bin/smoke.sh`)。
2. **P0 理论层**(§2):`theory_screen.py` + `eval/theory/` + 单测(喂 2-3 个 agent-bench entry 固定样例)。
3. **P1 执行层接线**(§3):`run_pool.sh --from-shortlist` + `report_scenario.py`。
4. **P1 文档**(§4)。
5. **验收**:`--domain medical --axis agent` 端到端跑 `mini` 子集,产出一份场景报告;
   `grep` 确认全仓再无 routing/MoA 选型残留(零命中)。

## 6. 已定决策(无需再确认)

1. **routing 旧代码:直接 `git rm` 删除**,不归档(见 §1)。
2. **`theory_score`:纯启发式加权**,零 LLM 调用,公式与默认权重见 §2(权重落 `eval/METRICS.md`,可调)。
