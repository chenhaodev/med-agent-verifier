# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **medical-capability verifier / evaluation harness** for **local Ollama models** (Chinese-language
medical tasks). The goal (original briefs ingested below, § Original task briefs) is to measure how well
local models do on medicine, scored against
two **trusted gold sources**:

1. **Book-distilled "serious" agents** (Track B, `gold_type=criteria`) — the sibling projects
   `../med-agent-internists/` (Cecil internal medicine) and `../med-agent-psy/` (DSM-5 psychiatry). These
   produce page-traceable, evidence-graded answers and serve as a high-quality reference. **Vendored as a
   snapshot** into `data/book-gold/{internists,psy}.yaml` (via `bin/sync_gold.sh`; provenance in
   `data/book-gold/SOURCE.md`) so the repo is **self-contained and reproducible** — the loader reads the
   snapshot and runs even with the siblings absent, falling back to the sibling live path only if a
   snapshot is missing. Refresh after siblings evolve: `./bin/sync_gold.sh` (paths configurable via
   `MED_AGENT_INTERNISTS`/`MED_AGENT_PSY`). Current scale: **235 questions** (183 internists + 52 psy) over
   **37 specialties** — verify with `python3 bin/load_dataset.py --track book --count`.
2. **MedBench Agent leaderboard outputs (95-point run)** (Track A, `gold_type=reference`) — the
   `data/medbench-agent-95/` directory: reference question/answer pairs from a top-scoring Agent
   submission. **360 records** (12 tasks × 30).

**Self-contained by design.** The only thing that still needs the sibling repos in `../` is the *optional*
`--track live` dynamic eval (`bin/eval_live.sh`/`run_sibling.sh`), which executes the sibling Agent for
fresh reference answers — it can't be vendored. All core static eval (Tracks A/B, leaderboard, hallucination,
calibration) runs purely from `data/` + `eval/`.

The core Phase-1 design questions are now **resolved and implemented**: the eval set is the two
gold tracks above; the **unified data interface** is `bin/load_dataset.py` (normalizes both into one record
discriminated by `gold_type`); **controllable subsets** are `--track/--task/--domain/--id/--limit/--sample`.

**Status: Phase 1 + TASK2 built & verified** — bash-orchestrated, LLM-as-a-Judge. The verifier produces a
**cross-model leaderboard** over local Ollama models that later feeds an **offline MoA** (route-to-top-k +
aggregate; built separately, see the TASK2 brief below). Layout: `bin/` scripts + `eval/` prompts & registry. See
`README.md` for the full picture. Key commands:

```bash
make help                                 # discoverable entry point: sync/check/test/lint/eval/leaderboard/calibrate/specialty/clean
pip install -r requirements.txt          # only pyyaml  (= make install)
cp .env.example .env                      # DEEPSEEK_API_KEY (judge) + OLLAMA_* (candidate)
./bin/sync_gold.sh                        # vendor Track B book gold → data/book-gold/ (= make sync; paths via MED_AGENT_* env)
python3 -m unittest discover -s tests     # 22-test stdlib suite (= make test; also run inside check.sh)
ruff check bin/*.py tests/                # lint: line-length 99, select E/F/I (= make lint)
./bin/check.sh                            # static gates (no judge budget): registry, both gold, Ollama smoke, TASK2 scripts
python3 bin/load_dataset.py --track medbench --task MedCOT --limit 1   # inspect one normalized record
./bin/eval.sh --track both --sample 3 --model qwen3.5                  # run: candidate (Ollama) → halluc check → judge (DeepSeek) → 0–40
./bin/eval.sh --track book --hallu --sample 3 --model qwen3.5          # + FActScore/HealthBench-Hallu atomic-claim hallucination (claim-level unsupported_rate + factual_precision)
./bin/eval.sh --subset mini --model qwen3.5                            # 30-question tiered mini-bench (hardest + most orthogonal)
python3 bin/select_subset.py                                          # (re)generate eval/subsets/{mini,medium,large}.yaml
python3 bin/specialty_report.py                                       # judge-free Track B 专科覆盖盘点（内科▸system▸domain / 精神科▸DSM，flags n<5）
python3 bin/calibrate_hallu.py                                        # 标定幻觉判官检测准确度 vs eval/calibration/hallu_gold.yaml（MedHallu 式 P/R/F1，含 hard 层）
# ── TASK2: leaderboard → routing, orchestrator eval, hallucination probes, live/freshness ──
python3 bin/gen_probes.py && python3 bin/gen_tool_decision.py         # (re)generate frozen probe sets
./bin/eval.sh --track probe --model qwen3.5                           # E: nonexistent + false-premise hallucination probes
./bin/eval.sh --track tool_decision --model qwen3.5                   # F2: tool-call decision (TIA, symmetric)
python3 bin/eval_routing.py --model qwen3.5                           # F1: specialty-routing accuracy (judge-free, 0 DeepSeek)
echo "我爸有高血压…" | ./bin/eval_live.sh --agent internists --model qwen3.5  # C: judge vs FRESH sibling answer
./bin/leaderboard.sh --md                                            # A: cross-model leaderboard (3 metric families, bootstrap CIs)
python3 bin/build_routing.py                                         # B: eval/routing_manifest.yaml (top-k + orchestrator)
./bin/freshness_audit.sh --domain cardiology                        # D: flag stale gold vs current guidelines (read-only)
```

**Three metric families — never blend** (separate scales): ① Capability (Track A, 0–40, per task,
⚠contamination-risk: public leaderboard data) · ② Specialty (Track B, 0–40, per domain + **per 科室
rollup** 内科/精神科, hallucination `unsupported_rate`) · ③ Orchestration & robustness (Accuracy %:
routing, TIA, probes, live). The leaderboard keeps these on distinct axes; `routing_manifest.yaml`
weights ③ above ① (contamination-resistant).

**Eval-criteria validity is documented + calibrated.** See **`eval/METRICS.md`** for the per-metric
validity writeup (what each measures → literature anchor → effect evidence → honest limitations). The
hallucination judge is **calibrated**: `bin/calibrate_hallu.py` scores `judge_prompt_hallu.md` against a
frozen hand-labeled set (`eval/calibration/hallu_gold.yaml`, easy + MedHallu-style *hard* subtle cases) →
MedHallu-style unsupported-detection **P/R/F1**. Live result (deepseek-v4-flash): **F1 1.0 overall and on
the hard tier** (subtle dose/number/mechanism/indication errors), FP=0 on subtle-true claims. Caveat: n=24
small; the judge is conservative (flags > abstains) — the right bias for a safety metric.

**Eval-criteria upgrades (literature-anchored).** The hallucination metric is now optionally measured the
way the literature does, not just homegrown: with `--hallu`, each Track B/A answer is **decomposed into
atomic clinical claims, each verified supported/unsupported/not_sure** (FActScore + HealthBench-Hallu
pattern; MedHallu's `not_sure` abstention class is **not** counted as hallucination). Headline =
`unsupported_rate = Σunsupported / Σclaims` + `factual_precision` (claim-level; leaderboard prefers it over
the coarse per-response `grounding_source` binary, tagged `unsupported_metric=claim`). Assets:
`eval/judge_prompt_hallu.md` + `bin/parse_hallu.py` (rates **recomputed from counts**, judge arithmetic
distrusted). Default **off** (extra judge call), so `check.sh`/normal runs keep budget. Separately, the
criteria judge now emits a **HealthBench context-awareness** signal — `context_awareness` ∈
{appropriate, overconfident, overhedged, na} + `seeks_clarification` — as an **additive label, NOT part of
the 40** (0–40 scale untouched); surfaced in eval summary + leaderboard diagnostic `ctx_appropriate_rate`.

**Why the repo isn't reorganized by 科室** (unlike `../med-agent-internists`): this is an eval **harness**,
not a knowledge agent — type-organization (`bin/` scripts + `eval/` prompts/registry) is the correct,
orthogonal layout. The specialty axis lives in **data + reporting**: Track B `domain` *is* the 科室,
`routing_manifest.yaml` already routes per-domain (the MoA "specialty→best model"), the leaderboard now
has a stable 内科/精神科 **rollup** (most domains are n<5 → per-domain is noise), and
`bin/specialty_report.py` gives a judge-free two-level coverage inventory. See its header for the rationale.

Candidate concurrency defaults to **1** (Ollama serializes; >1 trips curl timeout via queue wait). `--think
on|off` toggles a reasoning candidate's think-trace. **Tiered mini-bench** (`bin/select_subset.py`, **pure
deterministic, no external deps**): one ranking → nested `mini`(30) ⊂ `medium`(100) ⊂ `large`(all sentinel),
frozen in `eval/subsets/*.yaml`; "hardest" = model-free difficulty heuristic, "most orthogonal" =
`(track,task,domain)`-bucket round-robin (capability × specialty axes), with all 12 MedBench capabilities
guaranteed in `mini`. Regenerate after the siblings grow.

**TASK2 added (built & verified):** cross-model leaderboard (`bin/leaderboard.py`, bootstrap CIs +
length/self-preference/contamination diagnostics) → ranked routing manifest (`bin/build_routing.py`,
CI-overlap ties + `orchestrator:` nomination); hallucination overhaul (multi-source grounding judge +
`gen_probes.py` nonexistent/false-premise probes, blacklist demoted to safety floor); orchestrator eval
(`eval_routing.py` judge-free routing accuracy + `tool_decision` TIA); live-sibling dynamic eval
(`run_sibling.sh`/`eval_live.sh`); gold freshness audit (`freshness_audit.sh`, read-only). The MoA
inference system itself consumes `routing_manifest.yaml` and is **built separately**.

**Still deferred:** structured-task judge overrides (CallAPI/RetAPI/DBOps), crisis/OOB safety-interception
recall, MedEthics accuracy path (parser ready in `bin/parse_choice.py`; no jsonl yet — see
`eval/task_registry.yaml` `pending:`), live-WebSearch freshness (currently judge-knowledge; `/autoresearch`
upgrade), probe validity `--verify` for false-premise (currently `needs_review`, only `nonexistent` scored).

**Deferred — multi-model `--subset medium` run for usable per-domain routing.** The first real
multi-model leaderboard (5 models, 2026-06-11; qwen3.5:latest > :2b > :0.8b > qwen2.5:1.5b, strict
size-monotonic) was run on `mini`, where each specialty has **n=1** — below `build_routing.py`'s
`min_n=5`, so all manifest `domains:` are `insufficient_data` (MoA correctly falls back to `default`,
but per-科室 routing is unusable). To produce routable per-domain top-k, run the pool over `medium`
(~3/specialty) or `large`: `for m in <pool>; do ./bin/eval.sh --subset medium --model $m --think off;
done` → re-run leaderboard + build_routing (~1–1.5h/model). Also: family-3 tracks
(probe/routing/tool_decision/live) currently have full data only for qwen3.5:2b (TIA) + qwen2.5:1.5b —
re-run across the pool for a complete orchestration axis. **Excluded from the text pool:** vision models
(e.g. `minicpm-v4.6:1b`) — ~4× slower, garbage text scores, OLLAMA-error-prone.

## Original task briefs (formerly `TASK.md` / `TASK2.md`, ingested verbatim then removed)

**Phase 1 brief (was `TASK.md`)** — the founding question; everything in "What this is" answers it:

> 我想测一测本地ollama模型在医疗方面的能力。这里，我信任书本蒸馏得到的严肃智能体
> (../med-agent-internists, ../med-agent-psy/) 和当前MedBench打榜成果 (Agent榜单95分，
> medbench-agent-95/)。
>
> 那么如何建立一个评测集？统一化数据接口标准？可控/可控子集？

**TASK2 brief (was `TASK2.md`)** — the extension that produced leaderboard → routing → offline MoA:

> Ive finished TASK.md, that use a "book (../med-agent-internists)" and benchmark (Medbench-agent) to
> verify ollama models' correctness etc.
>
> Now, with this eval-tool, I can select the "right model for the right task", and I can integrate them
> as a system using ollama but NOT relying on APIs anymore.
>
> For above my extended goal, how shall I upgrade this eval-tool / this repo? Example: the "book
> (../med-agent-internists)" maybe old-knowledge, and I may consider /autoresearch + websearch as
> compensation solution. NOTE: maybe eval-tool shall be dynamic sometimes, running
> ../med-agent-internists for specific-disease QnA -- compare with ../med-agent-internists output etc
>
> NOTE: To be clear, verifier/judge should be DeepSeek -- but once finished eval on ollama LLMs, I will
> have leadboard on them, and I can use this info to build my offline llms (MOA) solution later-on

Both briefs are fully implemented (see Status above); "TASK2" survives as the name of the
leaderboard/routing/probe/live feature family in scripts and docs.

## The reference dataset: `data/medbench-agent-95/`

12 MedBench **Agent**-track task datasets, **30 records each**, paired `.jsonl` (data) + `.md` (task spec).

**JSONL record schema (uniform across all 12):**
```json
{"question": "...", "answer": "...", "other": {"id": <int>, "source": "<TaskName>_V4"}}
```
`question`/`answer` are long-form Chinese strings (clinical cases, API specs, dialogues). The `.md` for
each task gives its intro, the metadata contract, and one worked example.

**The 12 tasks** (capability under test → scoring metric stated in each `.md`):
- `MedCOT` — multi-step clinical reasoning chains · LLM-as-a-Judge
- `MedDecomp` — task decomposition · LLM-as-a-Judge
- `MedPathPlan` — clinical pathway planning · LLM-as-a-Judge
- `MedReflect` — self-reflection / error correction · LLM-as-a-Judge
- `MedCallAPI` — generate spec-conformant API call requests · LLM-as-a-Judge
- `MedRetAPI` — retrieval-style API use · LLM-as-a-Judge
- `MedDBOps` — clinical database operations · LLM-as-a-Judge
- `MedCollab` — multi-agent collaboration · LLM-as-a-Judge
- `MedLongConv` — long multi-turn conversation · LLM-as-a-Judge
- `MedLongQA` — long-context QA · LLM-as-a-Judge
- `MedShield` — risk identification + interception of unsafe requests · LLM-as-a-Judge
- `MedDefend` — adversarial/defense robustness · LLM-as-a-Judge

**Gotcha — the `.jsonl` and `.md` sets do not fully match.** `MedDefend.jsonl` has **no** `.md` spec;
`MedEthics.md` (a single-choice-question task scored by **Accuracy**, answer like `<D>`) has **no**
`.jsonl` data. 11 tasks overlap; budget for this mismatch in any loader that pairs them.

**Scoring is mostly LLM-as-a-Judge** (subjective long-form answers), **except MedEthics = Accuracy** (MCQ).
A verifier must therefore support a judge model, not just exact match.

## Environment

`ollama` is installed locally and hosts the candidate models (e.g. `glm-4.7-flash`, `qwen3.5`, `gpt-oss:20b`,
`qwen2.5:1.5b`, and medical fine-tunes like `Baichuan-M2-32B`, `SafeMed-R1`). Run `ollama list` to see
what's available. The candidate-under-test is a local Ollama model; the judge may be a stronger model
(local or the DeepSeek API used by the sibling projects).

## Sibling-project conventions (the pattern to reuse)

The `../med-agent-*` projects share a common architecture worth matching when building tooling here:

- **Bash orchestrates the pipeline; Python only for data work** (ingest/extract/audits). External LLM calls
  go through `bin/call_*.sh` with a **sha256 payload cache** under `.cache/`.
- **Gates before eval**: a `bin/check.sh` runs deterministic audits (routing, grounding, schema, smoke)
  that must all exit 0 before `bin/eval.sh` consumes API budget.
- **Determinism first** on the happy path (routing/scope gates are pure bash/regex, no API).
- **Traceability is the contract** — sibling answers carry `source_page`; preserve provenance when you
  pull their outputs in as gold.
- Minimal Python stack, `ruff.toml` lint (line-length 99, `select=["E","F","I"]`), DeepSeek config in
  `.env` (`DEEPSEEK_API_KEY`, …). `.cache/`, `.env`, `*.pdf`, eval result JSON are git-ignored.

See `../med-agent-internists/CLAUDE.md` for the fullest description of this shared pipeline.

## Conventions

- **Data is Chinese; no translation step.** Questions, answers, and source material are all Chinese.
- **Immutability**: when transforming records, emit new objects — never mutate the source `.jsonl` rows.
- **Don't conflate the two gold sources**: `data/medbench-agent-95/` is leaderboard reference output;
  the vendored `data/book-gold/` (from the `med-agent-internists/psy` agents) is book-grounded. They are
  different trust anchors — keep them distinguishable in any combined eval set.
