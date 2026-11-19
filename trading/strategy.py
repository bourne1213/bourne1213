# -*- coding: utf-8 -*-
"""
策略层：双均线金叉/死叉。

规则：
- 短期均线上穿长期均线（金叉）-> 买入信号 +1
- 短期均线下穿长期均线（死叉）-> 卖出信号 -1
- 其余时间 -> 空仓信号 0
"""
from __future__ import annotations

import pandas as pd

import config


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算均线并生成信号列，返回带列的新 DataFrame（不修改入参）。"""
    out = df.copy()
    out["ma_short"] = out["close"].rolling(config.SHORT_MA).mean()
    out["ma_long"] = out["close"].rolling(config.LONG_MA).mean()

    # 用均线相对位置判断：金叉当天 短线上穿长线
    prev = out["ma_short"].shift(1)
    prev_long = out["ma_long"].shift(1)
    golden_cross = (prev <= prev_long) & (out["ma_short"] > out["ma_long"])
    death_cross = (prev >= prev_long) & (out["ma_short"] < out["ma_long"])

    out["signal"] = 0
    out.loc[golden_cross, "signal"] = 1
    out.loc[death_cross, "signal"] = -1
    return out


def latest_signal(df: pd.DataFrame) -> dict | None:
    """返回最近一个交易日的信息 + 当天信号。无数据返回 None。"""
    if df.empty:
        return None
    ind = add_indicators(df)
    last = ind.iloc[-1]
    return {
        "date": last["date"],
        "close": round(float(last["close"]), 2),
        "ma_short": round(float(last["ma_short"]), 2),
        "ma_long": round(float(last["ma_long"]), 2),
        "signal": int(last["signal"]),
    }


SIGNAL_TEXT = {1: "买入", -1: "卖出", 0: "持有/观望"}


def describe(info: dict) -> str:
    return (
        f"收盘 {info['close']} | MA{config.SHORT_MA}={info['ma_short']} "
        f"MA{config.LONG_MA}={info['ma_long']} | 信号: {SIGNAL_TEXT[info['signal']]}"
    )
