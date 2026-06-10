#!/usr/bin/env python3
"""specialty_map.py — Track B domain → 临床专科分类（两级：broad_area ▸ system）。

设计目标：让「专科」成为**一等的报告/汇总轴**，对齐兄弟项目的科室组织
（med-agent-internists=内科按系统分章；med-agent-psy=精神科按 DSM-5 分章），
**而不必把 bin/eval 目录按科室重排**（见 specialty_report.py 头部的理由说明）。

健壮性优先：broad_area 主要由 **gold_source 路径**推断（兄弟新增 domain 时自动归类，
无需维护映射）；system 仅对内科子专科给出 西氏内科 式系统名，未知 domain 优雅降级为 "其他"。
"""

# 内科子专科 → 系统（西氏内科组织）。新 domain 缺失时降级 "其他内科"，不报错。
INTERNISTS_SYSTEM = {
    "cardiology": "循环系统",
    "respiratory": "呼吸系统",
    "digestive": "消化系统",
    "renal": "肾脏/泌尿",
    "hematology": "血液系统",
    "endocrine": "内分泌代谢",
    "infectious": "感染性疾病",
    "rheumatology": "风湿免疫",
    "neurology": "神经系统",
    "oncology": "肿瘤",
    "geriatrics": "老年医学",
    "palliative": "姑息/缓和医疗",
    "perioperative": "围手术期",
    "molecular": "分子/遗传",
    "womens_health": "女性健康",
    "mens_health": "男性健康",
    "med_induced": "药源性疾病",
}


def broad_area(domain, gold_source=None):
    """粗分：内科 | 精神科 | 其他。优先看 gold_source 路径（对兄弟扩张稳健）。"""
    src = (gold_source or "").lower()
    if "internist" in src:
        return "内科"
    if "psy" in src:
        return "精神科"
    # 无 gold_source 时回退：内科系统表命中 → 内科；否则按精神科兜底（psy domain 多样）
    if domain in INTERNISTS_SYSTEM:
        return "内科"
    return "其他"


def system_of(domain, gold_source=None):
    """细分：内科给系统名；精神科直接用 domain（已是 DSM-5 章级）。"""
    area = broad_area(domain, gold_source)
    if area == "内科":
        return INTERNISTS_SYSTEM.get(domain, "其他内科")
    if area == "精神科":
        return domain or "未分类精神科"
    return domain or "未分类"
