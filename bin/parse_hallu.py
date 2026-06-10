#!/usr/bin/env python3
"""parse_hallu.py — 解析原子声明级幻觉核查（FActScore / HealthBench-Hallu 范式）判官响应。

输入：判官原始响应文本（stdin）。
输出：规范化 JSON（stdout）：
    {"n_claims":N,"supported":N,"unsupported":N,"not_sure":N,
     "unsupported_rate":F,"factual_precision":F,"claims":[...],"flags":[...],
     "ok":bool,"error":str|null}

退出码：
    0  成功（从 claims 数组或汇总字段恢复出自洽计数）。
    3  无法提取任何 claim/计数 → 调用方可据此用 --no-cache 重跑判官一次。

设计：与 parse_judge.py 同源——严格 json.loads → 配平截取 → claims 数组兜底重算
（配平截取 extract_balanced / 严格解析 strict_parse 直接复用 parse_judge）。
计数以 **claims 数组实际 verdict 为准**（判官自报的 supported/unsupported 可能与数组不一致），
数组不可用时才退回判官自报的汇总字段。rate/precision 一律由计数**重算**，不信任判官算术。
"""
import json
import re
import sys

from parse_judge import extract_balanced, strict_parse

_VERDICTS = ("supported", "unsupported", "not_sure")


def _norm_verdict(v):
    """把 verdict 串归一到三类之一；无法识别返回 None。"""
    if not isinstance(v, str):
        return None
    s = v.strip().lower().replace("-", "_").replace(" ", "_")
    if s in _VERDICTS:
        return s
    if s in ("notsure", "unsure", "uncertain", "unknown"):
        return "not_sure"
    if s in ("support", "supported_", "true", "yes"):
        return "supported"
    if s in ("unsupport", "unsupported_", "false", "no", "hallucination", "hallucinated"):
        return "unsupported"
    return None


def _counts_from_claims(claims):
    """从 claims 数组按实际 verdict 计数（权威来源）。返回 (counts, kept_claims)。"""
    counts = {k: 0 for k in _VERDICTS}
    kept = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        v = _norm_verdict(c.get("verdict"))
        if v is None:
            continue
        counts[v] += 1
        kept.append({
            "claim": str(c.get("claim", ""))[:300],
            "verdict": v,
            "note": str(c.get("note", ""))[:200],
        })
    return counts, kept


def _regex_claims(raw):
    """兜底：从原始文本直接抓每个 claim 的 verdict（不依赖整体 JSON 良构）。"""
    counts = {k: 0 for k in _VERDICTS}
    for m in re.finditer(r'"verdict"\s*:\s*"([^"]+)"', raw):
        v = _norm_verdict(m.group(1))
        if v is not None:
            counts[v] += 1
    return counts


def _rates(counts):
    """由计数重算 rate/precision（不信任判官算术）。"""
    n = counts["supported"] + counts["unsupported"] + counts["not_sure"]
    denom = counts["supported"] + counts["unsupported"]
    unsupported_rate = round(counts["unsupported"] / n, 3) if n else 0.0
    factual_precision = round(counts["supported"] / denom, 3) if denom else 1.0
    return n, unsupported_rate, factual_precision


def parse(raw):
    """返回 (result_dict, ok)。ok=False 表示无法提取任何声明，建议重跑判官。"""
    raw = (raw or "").strip()
    obj_str = extract_balanced(raw)
    flags = []
    claims = []
    counts = None

    if obj_str:
        data = strict_parse(obj_str.replace("\n", " ").replace("\r", " "))
        if isinstance(data, dict):
            flags = list(data.get("flags", []) or [])
            arr = data.get("claims")
            if isinstance(arr, list) and arr:
                counts, claims = _counts_from_claims(arr)
            # claims 数组取不到 verdict → 退回判官自报汇总字段
            if (counts is None or sum(counts.values()) == 0) and all(
                k in data for k in ("supported", "unsupported", "not_sure")
            ):
                try:
                    counts = {k: int(data[k]) for k in _VERDICTS}
                except (TypeError, ValueError):
                    counts = None

    if counts is None or sum(counts.values()) == 0:
        rx = _regex_claims(raw)  # 兜底：原始文本逐 verdict 抓
        if sum(rx.values()) > 0:
            counts = rx
            if not flags:
                flags = ["claims 数组解析失败，按 verdict 正则兜底计数"]

    if counts is None or sum(counts.values()) == 0:
        return {
            "n_claims": 0, "supported": 0, "unsupported": 0, "not_sure": 0,
            "unsupported_rate": 0.0, "factual_precision": 1.0, "claims": [],
            "flags": flags or ["幻觉核查判官响应无法解析任何 claim"],
            "ok": False, "error": (obj_str or raw)[:200],
        }, False

    n, ur, fp = _rates(counts)
    return {
        "n_claims": n,
        "supported": counts["supported"],
        "unsupported": counts["unsupported"],
        "not_sure": counts["not_sure"],
        "unsupported_rate": ur,
        "factual_precision": fp,
        "claims": claims,
        "flags": flags,
        "ok": True,
        "error": None,
    }, True


def main():
    result, ok = parse(sys.stdin.read())
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
