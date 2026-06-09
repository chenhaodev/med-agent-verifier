# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **medical-capability verifier / evaluation harness** for **local Ollama models** (Chinese-language
medical tasks). The goal (`TASK.md`) is to measure how well local models do on medicine, scored against
two **trusted gold sources**:

1. **Book-distilled "serious" agents** — the sibling projects `../med-agent-internists/` (Cecil internal
   medicine) and `../med-agent-psy/` (DSM-5 psychiatry). These produce page-traceable, evidence-graded
   answers and serve as a high-quality reference.
2. **MedBench Agent leaderboard outputs (95-point run)** — the `medbench-agent-95/` directory: reference
   question/answer pairs from a top-scoring Agent submission.

The open design questions (from `TASK.md`) are still being worked out: **how to build the eval set, what
the unified data interface standard is, and how to define controllable subsets.** Treat decisions about
schema and scoring as open until confirmed with the user.

**Status: greenfield.** Only `TASK.md` and the `medbench-agent-95/` reference data exist — there is no
build/lint/test/run tooling yet. When adding pipeline code, follow the sibling convention (see below)
unless told otherwise.

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
