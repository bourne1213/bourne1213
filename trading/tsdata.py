# -*- coding: utf-8 -*-
"""
市场元数据层（akshare）：交易日历，本地缓存。

说明：行业/市值原先走 Tushare，因 Tushare 120 积分档限频(1次/小时)且本环境
东财(eastmoney)接口被墙，现已不再单独取数：
- 行业 → 由 screener.guess_sector() 关键词猜测（无网络，够用）
- 市值 → 展示字段，已移除（不影响任何因子评分）

仅保留交易日历（新浪源，免费无 token）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

import config
import data as data_mod

AK_DIR = config.DATA_DIR / "ak"

# 失败冷却（应对 akshare 源偶发不可用）
_fail_at: dict[str, float] = {}
_COOLDOWN_SEC = 3600


def _cooling(key: str) -> bool:
    t = _fail_at.get(key)
    return bool(t and (time.time() - t) < _COOLDOWN_SEC)


def _mark_fail(key: str) -> None:
    _fail_at[key] = time.time()


def get_trade_calendar(force: bool = False) -> set[str]:
    """全部交易日（YYYY-MM-DD 集合），新浪交易日历，缓存到 data/ak/trade_cal.json。"""
    cache = AK_DIR / "trade_cal.json"
    if cache.exists() and not force:
        try:
            return set(json.loads(cache.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    if _cooling("tradecal"):
        raise RuntimeError("交易日历接口冷却中")
    try:
        import akshare as ak

        data_mod._apply_proxy_policy()
        df = ak.tool_trade_date_hist_sina()
        days = {pd.Timestamp(d).strftime("%Y-%m-%d") for d in df["trade_date"]}
    except Exception:  # noqa: BLE001
        _mark_fail("tradecal")
        raise
    AK_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(sorted(days)), encoding="utf-8")
    return days


def next_trade_date(date_str: str) -> str:
    """返回 date_str 之后的下一个交易日（YYYY-MM-DD）。"""
    days = get_trade_calendar()
    d = pd.Timestamp(date_str) + pd.Timedelta(days=1)
    for _ in range(20):
        if d.strftime("%Y-%m-%d") in days:
            return d.strftime("%Y-%m-%d")
        d += pd.Timedelta(days=1)
    return str(d.date())


def last_trade_date() -> str:
    """返回今天或之前最近的一个交易日（YYYY-MM-DD）。"""
    days = get_trade_calendar()
    d = pd.Timestamp.today().normalize()
    for _ in range(20):
        s = d.strftime("%Y-%m-%d")
        if s in days:
            return s
        d -= pd.Timedelta(days=1)
    return pd.Timestamp.today().strftime("%Y-%m-%d")
