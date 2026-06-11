#!/usr/bin/env python3
"""model_pool.py — 候选模型池 loader：eval/model_pool.yaml → 归一化条目（含校验）。

谁用：bin/run_pool.sh 经 --tsv 消费（bash 编排、Python 管数据，仓库约定）；
check.sh 经 import 做 schema 门禁；人用 --list 看名单。

用法：
  python3 bin/model_pool.py --list          # 人读表（含 disabled，标 ✗）
  python3 bin/model_pool.py --tsv           # enabled 条目 TSV：name\tbackend\tthink
  python3 bin/model_pool.py --tsv --all     # 含 disabled
"""
import argparse
import os
import sys

import yaml

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL_FILE = os.path.join(ROOT_DIR, "eval", "model_pool.yaml")

ALLOWED_BACKENDS = ("ollama", "openai", "siliconflow", "litellm")
ALLOWED_THINK = ("", "on", "off")


def load_pool(path=POOL_FILE):
    """读池文件 → 归一化条目列表（defaults 合并、schema 校验、不可变：每条目新建 dict）。

    返回 [{name, backend, think, enabled, notes}, ...]；schema 非法抛 ValueError。
    """
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), list):
        raise ValueError(f"{path}: 顶层须为 dict 且含 models 列表")
    defaults = doc.get("defaults") or {}

    entries = []
    seen = set()
    for i, raw in enumerate(doc["models"]):
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ValueError(f"{path}: models[{i}] 缺 name")
        entry = {
            "name": str(raw["name"]),
            "backend": str(raw.get("backend", defaults.get("backend", "ollama"))),
            "think": str(raw.get("think", defaults.get("think", "")) or ""),
            "enabled": bool(raw.get("enabled", True)),
            "notes": str(raw.get("notes", "")),
        }
        if entry["backend"] not in ALLOWED_BACKENDS:
            raise ValueError(f"{path}: models[{i}] backend 非法：{entry['backend']}")
        if entry["think"] not in ALLOWED_THINK:
            raise ValueError(f"{path}: models[{i}] think 非法：{entry['think']}")
        key = (entry["name"], entry["backend"])
        if key in seen:
            raise ValueError(f"{path}: 重复条目 {key}")
        seen.add(key)
        entries.append(entry)
    return entries


def main():
    ap = argparse.ArgumentParser(description="候选模型池 loader")
    ap.add_argument("--pool", default=POOL_FILE, help="池文件路径（默认 eval/model_pool.yaml）")
    ap.add_argument("--tsv", action="store_true", help="输出 TSV：name\\tbackend\\tthink")
    ap.add_argument("--list", action="store_true", help="人读表")
    ap.add_argument("--all", action="store_true", help="含 enabled=false 条目")
    args = ap.parse_args()

    try:
        entries = load_pool(args.pool)
    except (OSError, ValueError, yaml.YAMLError) as e:
        print(f"错误：{e}", file=sys.stderr)
        sys.exit(1)
    if not args.all:
        entries = [e for e in entries if e["enabled"]]

    if args.tsv:
        for e in entries:
            print(f"{e['name']}\t{e['backend']}\t{e['think']}")
    else:  # 默认 --list
        print(f"{'':2} {'模型':<52} {'后端':<12} {'think':<6} 备注")
        for e in entries:
            mark = "✓" if e["enabled"] else "✗"
            print(f"{mark:2} {e['name']:<52} {e['backend']:<12} "
                  f"{e['think'] or '-':<6} {e['notes']}")
        n_on = sum(1 for e in entries if e["enabled"])
        print(f"\n共 {len(entries)} 条目，enabled {n_on}")


if __name__ == "__main__":
    main()
