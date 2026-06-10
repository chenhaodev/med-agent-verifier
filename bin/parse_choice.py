#!/usr/bin/env python3
"""parse_choice.py — 从模型自由文本里**确定性**抽取单选答案。零 API、零依赖。

两用途：
  - F1 路由：从给定专科列表里抽模型选了哪个（match_choice）。
  - 预留 MedEthics：抽 <A>/<B> 式字母选项（extract_letter）。

设计：判官无关、纯解析，供 eval_routing.py 与（将来）MedEthics accuracy 路径复用。
作为库导入（match_choice / extract_letter），也可 CLI 自测。
"""
import re
import sys

_LETTER_RE = re.compile(r"<\s*([A-Ea-e])\s*>")
_LETTER_FALLBACK = re.compile(r"(?:答案|选择|answer)[：:\s]*([A-Ea-e])\b", re.IGNORECASE)


def extract_letter(text):
    """抽取 <X> 或「答案：X」式单选字母（大写）。取不到返回 None。"""
    if not text:
        return None
    m = _LETTER_RE.search(text)
    if m:
        return m.group(1).upper()
    m = _LETTER_FALLBACK.search(text)
    return m.group(1).upper() if m else None


def match_choice(text, options):
    """从 options 里找出现在 text 中的那一个；多个命中取**最早出现**者。取不到返回 None。

    大小写不敏感，按 word/子串匹配。options 形如 ['cardiology','psychiatry',...]。
    刻意取最早出现：模型常先给结论再解释（解释里可能带其它科名）。
    """
    if not text or not options:
        return None
    low = text.lower()
    best, best_pos = None, len(low) + 1
    for opt in options:
        o = str(opt).lower()
        pos = low.find(o)
        if 0 <= pos < best_pos:
            best, best_pos = opt, pos
    return best


def main():
    # CLI 自测：echo "...回答..." | parse_choice.py opt1 opt2 ...
    text = sys.stdin.read()
    options = sys.argv[1:]
    if options:
        print(match_choice(text, options) or "")
    else:
        print(extract_letter(text) or "")


if __name__ == "__main__":
    main()
