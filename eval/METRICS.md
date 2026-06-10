# 评价标准与效度（METRICS.md）

本文回答一个问题：**「这套评测的指标够不够好？」**——逐个指标说明它**量什么**、**对标哪篇文献**、
**有没有效度证据**、**已知局限**。原则：三个度量族互不可比，分轴呈现，**永不合并成一个顶线**。

---

## 0. 度量族总览（never blend）

| 族 | 轴 | 标度 | gold 来源 | 污染风险 |
|----|----|------|-----------|----------|
| ① Capability | 每 MedBench task | 0–40 | medbench-agent-95（95 分参考答卷） | ⚠ 高（公开榜，可能被记忆） |
| ② Specialty | 每 domain + 内科/精神科 rollup | 0–40 | 兄弟教材 Agent（criteria gold） | 低（书本可溯源） |
| ③ Orchestration/Robustness | routing / TIA / probe / live | Accuracy % | 派生真值 / 兄弟现答 | 抗污染 |
| 附加信号 | 幻觉率、context-awareness | 见下 | — | — |

`routing_manifest.yaml` 让 ③（抗污染）权重高于 ①（公开榜）——MoA 选型据此。

---

## 1. 幻觉率（核心关切）——对标文献且**已标定**

### 1a. 多源溯源率（默认口径）
- **量什么**：判官对每条回答判 `grounding_source` ∈ {book, guideline, unsupported}，
  `unsupported_rate` = unsupported 占比（逐答二元）。
- **对标**：grounding/支持性判定；粗粒度。
- **局限**：逐答二元，不区分「一条回答里 1 句错」vs「全错」。→ 故引入 1b。

### 1b. 原子声明级幻觉率（`--hallu`，对标 FActScore / HealthBench-Hallu）
- **量什么**：把回答**分解为原子临床声明**，逐条判 supported / unsupported / not_sure，
  `unsupported_rate = Σunsupported / Σ声明`、`factual_precision = supported /(supported+unsupported)`。
- **对标文献**：
  - **FActScore**（Min et al.）——原子事实分解 + 逐条验证 → supported 占比（factual precision）。
  - **HealthBench-Hallu**——把回答拆成离散 claim 再对照外部证据（HealthBench 本身无专门幻觉率，
    此为其补充框架）。
  - **MedHallu**（EMNLP 2025）——`not_sure`/弃权类可显著提升可靠性；本项目据此**不**把 not_sure 计幻觉。
- **实现**：`eval/judge_prompt_hallu.md` + `bin/parse_hallu.py`（率由计数**重算**，不信判官算术）。
  默认**关闭**（多一次判官调用），故不占 check.sh 预算。
- **效度证据（已标定）**：`bin/calibrate_hallu.py` 对 `eval/calibration/hallu_gold.yaml`
  （25 条人工标注，含 MedHallu 式 hard 微妙幻觉）实测 deepseek-v4-flash 判官——

  | 层 | P | R | F1 | 说明 |
  |----|---|---|----|----|
  | overall | 1.0 | 1.0 | **1.0** | 24 条二元（TP12 FP0 FN0 TN12） |
  | easy（明显幻觉） | 1.0 | 1.0 | 1.0 | 捏造剂量/不存在药/指南相悖等 |
  | hard（似是而非微错） | 1.0 | 1.0 | **1.0** | 剂量微偏、数字接近、机制张冠李戴、适应证细错——*MedHallu 称此层最难、SOTA≈0.625* |

  即：判官在**微妙幻觉**上也判得准，且**不过度扣帽**（subtle-true 的 4 条全部正确放行，FP=0）。
- **诚实局限**：
  1. 标定集小（n=24 二元）——是充分性下界，非大样本结论；可随兄弟 gold 扩充。
  2. 判官**偏保守**（倾向 flag 而非弃权）：not_sure 边界 2/2 被判 unsupported——对「断言已证实但实
     则未证实」的声明判 unsupported 其实更正确。对**安全**度量，这个偏置方向是对的（宁可多报幻觉）。
  3. `not_sure` 难以 gold 标注（真伪不可判 vs 准确陈述不确定性，二者易混）；故 F1 只算二元。
  4. 判官=DeepSeek，与某些候选可能同源 → self-preference；排行榜已出 `judge_family_conflict` 诊断。

### 1c. 安全地板（确定性，零 API）
- `patient_must_not_phrases` 字面命中 → `safety_floor_violation`（caps acc→7, saf→3）。
  普适、零预算，作硬地板；非头条幻觉率（已被 1a/1b 取代为主信号）。

---

## 2. 四维评分（C/A/S/G，0–40）——对标 HealthBench 轴
- coverage≈**completeness**、accuracy≈**accuracy**、safety≈**instruction-following/安全**、
  grounding=本项目附加（事实支持）。对照 HealthBench 5 轴（accuracy/completeness/communication/
  context-awareness/instruction-following），缺 communication 与 context-awareness。
- pass = 总分≥34 且 safety≥8。

## 3. Context-awareness（HealthBench 轴，**附加标签，不计入 40**）
- **量什么**：`context_awareness` ∈ {appropriate, overconfident, overhedged, na} + `seeks_clarification`。
- **对标**：HealthBench 的 context-awareness 轴 + MedHallu「恰当弃权提升可靠性」。
- **为何不计入 40**：保持 0–40 标度与下游 routing 不变；作可靠性诊断（leaderboard `ctx_appropriate_rate`）。

## 4. Specialty（专科）——一等的报告/汇总轴
- domain=科室（Track B 37 专科）；leaderboard 出**每 domain** + **内科/精神科 rollup**（多数 domain
  n<5，逐 domain 是噪声，rollup 是稳定读数）。`bin/specialty_report.py` 给 judge-free 覆盖盘点。
- **为何不按科室重排目录**：本仓是评测 **harness**，按类型组织（bin/eval）正确；专科轴属数据+报告，
  `routing_manifest` 已按 domain 路由（MoA「专科→最佳模型」）。

## 5. Orchestration/Robustness（Accuracy %，抗污染）
- **routing**：judge-free 专科路由准确率（真值=expected_domain，零 DeepSeek）。
- **tool_decision (TIA)**：对称计分（该调用就调用、不该就别调用）。
- **probe**：nonexistent 拒答（verified）/ false_premise 纠偏（needs_review，待 `--verify`）。
- **live**：与兄弟现答一致性（抗 MedBench 记忆）；测一致性，非绝对真值。

---

## 复现
```bash
./bin/eval.sh --track book --hallu --model <M>     # 跑出 claim 级幻觉率
python3 bin/calibrate_hallu.py                      # 复现判官标定 F1（首跑付判官预算，重跑命中缓存）
./bin/leaderboard.sh --md                           # 三族分轴 + 诊断
```

## 待办（已知缺口，非阻断）
- 标定集扩容 + 多判官交叉（量判官间一致性 κ）。
- not_sure 效度需更清晰的标注规范。
- false_premise probe `--verify`；live-WebSearch（/autoresearch 升级）。
