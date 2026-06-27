#!/usr/bin/env python3
"""filter_shortlist.py — 把 run_pool 的池 TSV 按阶段1理论短名单过滤(阶段1→阶段2 接缝)。

stdin  : model_pool.py --tsv 输出（name\\tbackend\\tthink，每行一个本地候选）
argv[1]: eval/theory/shortlist.yaml（theory_screen.py 产物）
stdout : 只保留 tier∈{must-test,optional} 且 testable=true 的候选行（原样透传）
stderr : missing_local 告警（理论想测但本地池没有的候选）

设计：纯过滤、不改写行（不可变）；理论与本地池的差集如实报出，不静默吞。
"""
import sys

import yaml

TEST_TIERS = ("must-test", "optional")


def wanted_models(shortlist_path):
    with open(shortlist_path, encoding="utf-8") as f:
        sl = yaml.safe_load(f) or {}
    out = []
    for c in sl.get("candidates") or []:
        if c.get("testable") and c.get("tier") in TEST_TIERS:
            out.append(str(c.get("model", "")))
    return [m for m in out if m]


def main():
    if len(sys.argv) != 2:
        sys.exit("用法：model_pool.py --tsv | filter_shortlist.py <shortlist.yaml>")
    wanted = wanted_models(sys.argv[1])
    if not wanted:
        print("告警：短名单无 testable 的 must-test/optional 候选。", file=sys.stderr)
    local = {}
    kept = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        name = line.split("\t", 1)[0]
        local[name] = line
        if name in wanted:
            kept.append(line)
    for m in wanted:
        if m not in local:
            print(f"missing_local: {m}（理论想测，本地池无——先 ollama pull 或加进 pool）",
                  file=sys.stderr)
    print("\n".join(kept))


if __name__ == "__main__":
    main()
