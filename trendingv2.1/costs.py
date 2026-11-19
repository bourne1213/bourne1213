# -*- coding: utf-8 -*-
"""
交易成本：佣金 + 印花税（供回测、模拟盘、组合回测共用，避免各处重复实现）。
"""
from __future__ import annotations

import config


def commission(amount: float) -> float:
    """佣金：双边，万3，最低 5 元。"""
    return max(amount * config.COMMISSION_RATE, 5.0)


def stamp_tax(amount: float) -> float:
    """印花税：卖出单边。"""
    return amount * config.STAMP_TAX_RATE
