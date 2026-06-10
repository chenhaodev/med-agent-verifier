# data/ — 评测数据（两路 gold，互不混同）

本目录是 med-agent-verifier 的**数据层**。把数据从仓库根下沉到此处，并把 Track B 的 book gold
**vendored 为快照**，使本仓**自包含、可复现**——核心静态评测不再依赖兄弟项目在位。

```
data/
├─ medbench-agent-95/      Track A（gold_type=reference）：MedBench Agent 95 分答卷
│                          12 任务 × 30 题的 .jsonl + .md 规约（公开榜，⚠ 有记忆/污染风险）
└─ book-gold/             Track B（gold_type=criteria）：教材派姊妹 Agent 的 gold 快照
   ├─ internists.yaml      内科（西氏内科精要），183 题
   ├─ psy.yaml             精神科（DSM-5），52 题
   └─ SOURCE.md            provenance：各快照的源路径 / 同步时间 / 兄弟 git commit / 题数
```

## 两路 gold 是不同的信任锚（勿混同）
- **medbench-agent-95/**：top Agent 提交的**参考答卷**——衡量「能力广度」（每任务 0–40）。
- **book-gold/**：逐句可溯源到教材页码的**评判标准**——衡量「临床诚信 + 专科深度」
  （幻觉率、逐专科短板）。

## 刷新 book gold 快照
book gold 是会演进的单一真相源（在兄弟仓 `../med-agent-internists`、`../med-agent-psy`）。
评测应对**固定快照**跑以保证可复现；兄弟更新后手动刷新：

```bash
make sync                 # = ./bin/sync_gold.sh，从默认 ../med-agent-* 同步
MED_AGENT_INTERNISTS=/path MED_AGENT_PSY=/path make sync   # 自定义兄弟路径
```

`load_dataset.py` 默认读这里的快照；快照缺失时才回退到兄弟 live 路径（并提示跑 sync）。
**唯一**仍需兄弟在位的是可选的 `--track live`（实时执行兄弟 Agent，无法 vendored）。

> 数据为中文，不做翻译。转换记录时一律产出新对象，绝不就地修改源 `.jsonl/.yaml`。
