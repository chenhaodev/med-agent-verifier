#!/usr/bin/env python3
"""load_dataset.py — 统一化数据接口标准（unified record loader）。

把两路 gold 源归一化为同一条下游记录，由 `gold_type` 判别：

  Track A — MedBench（capability breadth）
    medbench-agent-95/<Task>.jsonl: {question, answer, other:{id, source}}
    → gold_type="reference"（answer = 95 分参考答案，judge 看得到）

  Track B — Book gold（hallucination + specialty depth）
    ../med-agent-internists/eval/gold.yaml (+ psy) 的 criteria 字段
    → gold_type="criteria"（无整篇参考，按 expected_topics / must_warn /
      source_refs / must_not / patient_must_not_phrases 评判）

输出：每行一条归一化 JSON 记录（JSONL），供 eval.sh 切片成 q_*.json。

可控子集（可控子集 / controllable subsets）通过过滤标志实现：
  --track book|medbench   --task <name>   --domain <specialty>
  --id <id>   --limit N   --sample N（每个分组前 N 条，确定性）

不可变性：只读 gold 源，逐条 emit 新对象，从不就地修改原始行。
"""
import argparse
import glob
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# Track A：MedBench Agent 数据目录（仓库内快照）
MEDBENCH_DIR = os.path.join(ROOT_DIR, "medbench-agent-95")

# Track B：兄弟项目 gold（live 相对路径，单一真相源，不复制）
BOOK_SOURCES = [
    ("internists", os.path.join(ROOT_DIR, "..", "med-agent-internists", "eval", "gold.yaml")),
    ("psy", os.path.join(ROOT_DIR, "..", "med-agent-psy", "eval", "gold.yaml")),
]


def _domain_of(q):
    """expected_domain[0] 的 specialty 段（'cardiology:hypertension' → 'cardiology'）。"""
    doms = q.get("expected_domain") or []
    if not doms:
        return None
    first = doms[0]
    return str(first).split(":")[0] if first else None


def load_medbench():
    """归一化 Track A：每个 <Task>.jsonl 的每条记录 → reference 型统一记录。"""
    records = []
    for path in sorted(glob.glob(os.path.join(MEDBENCH_DIR, "*.jsonl"))):
        task = os.path.splitext(os.path.basename(path))[0]
        rel = os.path.relpath(path, ROOT_DIR)
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                other = row.get("other") or {}
                records.append({
                    "track": "medbench",
                    "task": task,
                    "id": other.get("id"),
                    "domain": None,
                    "mode": None,
                    "gold_type": "reference",
                    "metric": "judge",
                    "question": row.get("question", ""),
                    "reference": row.get("answer", ""),
                    "gold_source": rel,
                })
    return records


def load_book():
    """归一化 Track B：兄弟 gold.yaml 的每题 → criteria 型统一记录。

    兄弟两套 key-set 略异（psy 有 mode、无 doctor_must_have_tags；internists 反之）；
    loader 一律 .get 容错，结构性 tag 不进 criteria（Phase 1 候选只收原始问题，
    must_not/patient_must_not_phrases 幻觉检查则普适）。
    """
    import yaml  # 延迟导入：仅 Track B 需要

    records = []
    for task, path in BOOK_SOURCES:
        if not os.path.exists(path):
            print(f"警告：Track-B gold 不存在，跳过：{path}", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for q in data.get("questions", []):
            records.append({
                "track": "book",
                "task": task,
                "id": q.get("id"),
                "domain": _domain_of(q),
                "mode": q.get("mode"),
                "gold_type": "criteria",
                "metric": "judge",
                "question": q.get("question", ""),
                "criteria": {
                    "expected_topics": list(q.get("expected_topics", []) or []),
                    "must_warn": list(q.get("must_warn", []) or []),
                    "source_refs": list(q.get("source_refs", []) or []),
                    "must_not": list(q.get("must_not", []) or []),
                    "patient_must_not_phrases": list(
                        q.get("patient_must_not_phrases", []) or []
                    ),
                },
                "gold_source": os.path.relpath(path, ROOT_DIR),
            })
    return records


def _apply_subset(records, name):
    """按命名清单 eval/subsets/<name>.yaml 取片，并保持清单内顺序（mini-bench 固定基准）。

    `all: true`（large）= 动态全量，原样返回。否则按 (track, task, str(id)) 精确匹配清单 refs。
    """
    import yaml

    path = os.path.join(ROOT_DIR, "eval", "subsets", f"{name}.yaml")
    if not os.path.exists(path):
        print(f"错误：子集清单不存在：{path}", file=sys.stderr)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}
    if manifest.get("all"):
        return records
    by_key = {(r["track"], r["task"], str(r["id"])): r for r in records}
    out, missing = [], []
    for ref in manifest.get("records", []) or []:
        key = (ref["track"], ref["task"], str(ref["id"]))
        if key in by_key:
            out.append(by_key[key])
        else:
            missing.append(key)
    if missing:
        print(f"警告：子集 {name} 有 {len(missing)} 条 ref 在当前 gold 中找不到"
              f"（gold 可能已变动，建议重跑 select_subset.py）：{missing[:3]}…", file=sys.stderr)
    return out


def _filter(records, args):
    """确定性过滤 → 可控子集。"""
    out = records
    if getattr(args, "subset", None):
        out = _apply_subset(out, args.subset)
    if args.task:
        tasks = {t.strip() for t in args.task.split(",") if t.strip()}
        out = [r for r in out if r["task"] in tasks]
    if args.domain:
        doms = {d.strip() for d in args.domain.split(",") if d.strip()}
        out = [r for r in out if r.get("domain") in doms]
    if args.id:
        out = [r for r in out if str(r.get("id")) == str(args.id)]
    if args.sample:
        seen = {}
        sampled = []
        for r in out:  # 每个 (track,task,domain) 分组取前 N，按源顺序，确定性
            key = (r["track"], r["task"], r.get("domain"))
            if seen.get(key, 0) < args.sample:
                sampled.append(r)
                seen[key] = seen.get(key, 0) + 1
        out = sampled
    if args.limit is not None:
        out = out[: args.limit]
    return out


def main():
    ap = argparse.ArgumentParser(description="统一化 gold 加载器（Track A + B）")
    ap.add_argument("--track", choices=["book", "medbench", "both"], default="both")
    ap.add_argument("--subset", help="命名子集清单 eval/subsets/<name>.yaml（mini/medium/large）")
    ap.add_argument("--task", help="逗号分隔的 task 名（MedCOT / internists / psy …）")
    ap.add_argument("--domain", help="逗号分隔的 specialty（仅 Track B，如 cardiology）")
    ap.add_argument("--id", help="精确匹配单条记录 id")
    ap.add_argument("--limit", type=int, help="总条数上限（过滤后取前 N）")
    ap.add_argument("--sample", type=int, help="每个 task/domain 分组前 N 条（确定性）")
    ap.add_argument("--count", action="store_true", help="只打印条数，不输出记录")
    args = ap.parse_args()

    records = []
    if args.track in ("medbench", "both"):
        records += load_medbench()
    if args.track in ("book", "both"):
        records += load_book()

    # --domain 是 Track-B（专科）概念；medbench 记录 domain=None 必被它剔除。
    # 这是合理的（按专科取片），但若用户没意识到会得到「静默的半场跑」——故显式告警。
    if args.domain and any(r["track"] == "medbench" for r in records):
        n_mb = sum(1 for r in records if r["track"] == "medbench")
        print(
            f"提示：--domain 是 Track-B 专科过滤，已排除全部 {n_mb} 条 medbench(Track-A) 记录"
            f"（仅评 book）。如需 Track-A 请去掉 --domain 或显式 --track medbench。",
            file=sys.stderr,
        )

    records = _filter(records, args)

    if args.count:
        print(len(records))
        return

    for r in records:
        print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
