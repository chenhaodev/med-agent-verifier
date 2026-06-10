# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **medical-capability verifier / evaluation harness** for **local Ollama models** (Chinese-language
medical tasks). The goal (`TASK.md`) is to measure how well local models do on medicine, scored against
two **trusted gold sources**:

1. **Book-distilled "serious" agents** (Track B, `gold_type=criteria`) — the sibling projects
   `../med-agent-internists/` (Cecil internal medicine) and `../med-agent-psy/` (DSM-5 psychiatry). These
   produce page-traceable, evidence-graded answers and serve as a high-quality reference. Read **live**
   from `../med-agent-*/eval/gold.yaml` (no copy). Current scale: **235 questions** (183 internists +
   52 psy) over **37 specialties** — counts grow as the siblings evolve, so verify with
   `python3 bin/load_dataset.py --track book --count`.
2. **MedBench Agent leaderboard outputs (95-point run)** (Track A, `gold_type=reference`) — the
   `medbench-agent-95/` directory: reference question/answer pairs from a top-scoring Agent submission.
   **360 records** (12 tasks × 30).

The core `TASK.md` design questions are now **resolved and implemented (Phase 1)**: the eval set is the two
gold tracks above; the **unified data interface** is `bin/load_dataset.py` (normalizes both into one record
discriminated by `gold_type`); **controllable subsets** are `--track/--task/--domain/--id/--limit/--sample`.

**Status: Phase 1 + TASK2 built & verified** — bash-orchestrated, LLM-as-a-Judge. The verifier produces a
**cross-model leaderboard** over local Ollama models that later feeds an **offline MoA** (route-to-top-k +
aggregate; built separately, see `TASK2.md`). Layout: `bin/` scripts + `eval/` prompts & registry. See
`README.md` for the full picture. Key commands:

```bash
pip install -r requirements.txt          # only pyyaml
cp .env.example .env                      # DEEPSEEK_API_KEY (judge) + OLLAMA_* (candidate)
ruff check bin/*.py                       # lint: line-length 99, select E/F/I
./bin/check.sh                            # static gates (no judge budget): registry, both gold, Ollama smoke, TASK2 scripts
python3 bin/load_dataset.py --track medbench --task MedCOT --limit 1   # inspect one normalized record
./bin/eval.sh --track both --sample 3 --model qwen3.5                  # run: candidate (Ollama) → halluc check → judge (DeepSeek) → 0–40
./bin/eval.sh --subset mini --model qwen3.5                            # 30-question tiered mini-bench (hardest + most orthogonal)
python3 bin/select_subset.py                                          # (re)generate eval/subsets/{mini,medium,large}.yaml
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
⚠contamination-risk: public leaderboard data) · ② Specialty (Track B, 0–40, per domain, grounding-based
`unsupported_rate`) · ③ Orchestration & robustness (Accuracy %: routing, TIA, probes, live). The leaderboard
keeps these on distinct axes; `routing_manifest.yaml` weights ③ above ① (contamination-resistant).

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

## The reference dataset: `medbench-agent-95/`

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
- **Don't conflate the two gold sources**: `medbench-agent-95/` is leaderboard reference output;
  the `med-agent-internists/psy` agents are book-grounded. They are different trust anchors — keep them
  distinguishable in any combined eval set.
