# -*- coding: utf-8 -*-
"""
消息面情绪分析（词典法）。

- 对新闻文本打分：正面/负面关键词命中 -> [-1, 1] 情绪分。
- 对时政/宏观新闻做「政策 -> 板块 -> 方向」映射，得到各板块的情绪分。

说明：这是可解释的启发式方法，不含模型；如需更强语义理解，
可在 config 里配置 LLM_API 后改用大模型打分（见 config 注释）。
"""
from __future__ import annotations

import re

# ---------- 通用正/负面词 ----------
POSITIVE_WORDS = [
    "利好", "增长", "上涨", "大涨", "突破", "盈利", "回暖", "复苏", "反弹",
    "支持", "扶持", "降准", "降息", "宽松", "刺激", "签约", "中标", "获批",
    "创新高", "超预期", "回购", "增持", "分红", "补贴", "改善", "向好",
    "放量", "景气", "提速", "加码", "减免", "下调利率", "上调评级",
]
NEGATIVE_WORDS = [
    "利空", "下跌", "大跌", "亏损", "下滑", "减持", "处罚", "罚款", "调查",
    "立案", "违约", "暴雷", "退市", "加息", "收紧", "制裁", "关税",
    "贸易摩擦", "风险", "预警", "质押", "爆仓", "暴跌", "恶化", "裁员",
    "下调评级", "停产", "诉讼", "冻结", "警示函", "监管函", "约谈",
]

# ---------- 政策/时政 -> (板块, 方向) ----------
# 命中关键词后，给对应板块累积 ±1 的方向分
POLICY_RULES = [
    ("降准", ["银行", "券商", "地产"], 1),
    ("降息", ["银行", "地产", "券商"], 1),
    ("宽松", ["银行", "券商"], 1),
    ("LPR", ["银行", "地产"], 1),
    ("加息", ["银行", "地产"], -1),
    ("收紧", ["地产", "券商"], -1),
    ("去杠杆", ["地产", "券商"], -1),
    ("新能源", ["新能源", "光伏", "风电", "储能"], 1),
    ("光伏", ["新能源", "光伏"], 1),
    ("风电", ["新能源", "风电"], 1),
    ("储能", ["新能源", "储能"], 1),
    ("碳中和", ["新能源", "环保"], 1),
    ("芯片", ["半导体", "电子"], 1),
    ("半导体", ["半导体"], 1),
    ("集成电路", ["半导体"], 1),
    ("军工", ["军工"], 1),
    ("国防", ["军工"], 1),
    ("促消费", ["消费", "白酒", "零售"], 1),
    ("内需", ["消费", "零售"], 1),
    ("消费补贴", ["消费", "零售"], 1),
    ("白酒", ["消费", "白酒"], 1),
    ("新基建", ["基建", "通信"], 1),
    ("基建", ["基建"], 1),
    ("房地产", ["地产"], 1),
    ("楼市", ["地产"], 1),
    ("集采", ["医药"], -1),
    ("医保谈判", ["医药"], -1),
    ("制裁", ["半导体", "出口"], -1),
    ("关税", ["出口", "制造"], -1),
    ("出口管制", ["半导体", "出口"], -1),
    ("贸易摩擦", ["出口", "制造"], -1),
]


def _count_hits(text: str, words: list[str]) -> int:
    return sum(text.count(w) for w in words if w in text)


def score_text(text: str) -> float:
    """单条文本情绪分 [-1, 1]，0 表示中性。"""
    if not text:
        return 0.0
    pos = _count_hits(text, POSITIVE_WORDS)
    neg = _count_hits(text, NEGATIVE_WORDS)
    if pos == 0 and neg == 0:
        return 0.0
    # 用 +1 阻尼，避免单次命中就给出 ±1 的极端值
    return round((pos - neg) / (pos + neg + 1), 3)


def score_news(texts: list[str]) -> float:
    """一组新闻的平均情绪分。"""
    if not texts:
        return 0.0
    scores = [score_text(t) for t in texts if t]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 3)


def sector_impacts(texts: list[str]) -> dict[str, float]:
    """从时政/宏观文本提取各板块情绪分（累加方向，再归一化到 [-1,1]）。"""
    acc: dict[str, float] = {}
    for text in texts:
        if not text:
            continue
        for keyword, sectors, direction in POLICY_RULES:
            if keyword in text:
                for s in sectors:
                    acc[s] = acc.get(s, 0.0) + direction
    # 归一化：除以最大绝对值，限制在 [-1, 1]
    if not acc:
        return {}
    m = max(abs(v) for v in acc.values()) or 1
    return {k: round(v / m, 3) for k, v in acc.items()}


def sentiment_for_sector(impacts: dict[str, float], sector: str) -> float:
    """查某板块的情绪分，未命中返回 0。"""
    return impacts.get(sector, 0.0)
