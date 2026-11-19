# -*- coding: utf-8 -*-
"""
确定性多因子评分引擎（第二阶段）。

因子（权重见 config.FACTOR_WEIGHTS）：
- trend      趋势：价格相对 MA20、MA20 相对 MA60
- rs         相对强度：20日收益 - 沪深300 20日收益
- mid_mom    中期动量：20日收益
- short_mom  短期动量：5日收益
- vol        波动控制：20日波动率（高=扣分）
- dd         回撤控制：20日最大回撤（深=扣分）
- liq        流动性：20日平均成交额（高=加分）
- news       新闻情绪：LLM 结构化评分（外部传入）

所有因子先做横截面百分位排名，再按权重加权，避免量纲问题。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def compute_raw_factors(df: pd.DataFrame, benchmark_ret20: float = 0.0) -> dict | None:
    """计算单只股票原始因子值；数据不足返回 None。"""
    if df is None or len(df) < config.LONG_MA + 40:
        return None
    close = df["close"]
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    ret5 = close.iloc[-1] / close.iloc[-6] - 1
    ret20 = close.iloc[-1] / close.iloc[-21] - 1

    logret = np.log(close / close.shift(1)).dropna()
    vol20 = float(logret.tail(20).std())

    window = close.tail(20)
    dd20 = abs(float((window / window.cummax() - 1).min()))  # 回撤深度（正数，越大越差）

    # 流动性近似：20日 成交量*收盘价 均值
    amount20 = float((df["volume"].tail(20) * close.tail(20)).mean())

    trend = (close.iloc[-1] / ma20.iloc[-1] - 1) + 0.5 * (ma20.iloc[-1] / ma60.iloc[-1] - 1)

    return {
        "trend": float(trend),
        "rs": float(ret20 - benchmark_ret20),
        "mid_mom": float(ret20),
        "short_mom": float(ret5),
        "vol": vol20,
        "dd": dd20,
        "liq": float(np.log1p(amount20)),  # 取对数压缩量纲
    }


def cross_section_rank(stocks: dict[str, dict]) -> dict[str, dict]:
    """
    对每只股票的每个因子做横截面百分位排名（0~1）。
    入参 stocks: {symbol: {factor_name: value}}。
    """
    if not stocks:
        return {}
    factor_names = list(next(iter(stocks.values())).keys())
    df = pd.DataFrame.from_dict(stocks, orient="index")
    ranks = {}
    for f in factor_names:
        col = pd.to_numeric(df[f], errors="coerce")
        r = col.rank(pct=True)  # 0~1，越大越好
        for sym in stocks:
            ranks.setdefault(sym, {})[f] = float(r.get(sym, 0.5)) if not pd.isna(r.get(sym, 0.5)) else 0.5
    return ranks


def composite_score(ranks: dict[str, dict], news_score: dict[str, float] | None = None) -> dict[str, float]:
    """
    加权合成总分（0~1）。
    vol / dd 越高越差，故取 (1 - rank)。
    news 因子若有 LLM 评分则纳入，否则权重归一化到其它因子。
    """
    w = dict(config.FACTOR_WEIGHTS)
    news_score = news_score or {}
    if not news_score:
        w.pop("news", None)
    total_w = sum(w.values())
    scores = {}
    for sym, rk in ranks.items():
        s = 0.0
        for f, wt in w.items():
            if f == "vol" or f == "dd":
                s += wt * (1.0 - rk.get(f, 0.5))  # 高波动/深回撤扣分
            elif f == "news":
                # news 传入的是 [-1,1]，映射到 0~1
                s += wt * ((news_score.get(sym, 0.0) + 1.0) / 2.0)
            else:
                s += wt * rk.get(f, 0.5)
        scores[sym] = s / total_w
    return scores


def benchmark_ret20(benchmark_df: pd.DataFrame) -> float:
    """沪深300 的 20日收益。"""
    if benchmark_df is None or len(benchmark_df) < 21:
        return 0.0
    c = benchmark_df["close"]
    return float(c.iloc[-1] / c.iloc[-21] - 1)
