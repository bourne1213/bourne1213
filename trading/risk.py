# -*- coding: utf-8 -*-
"""
卖出规则（风控）：回答「什么时候卖」。

对每笔持仓，按下列价格规则判断是否触发卖出（任一命中即卖）：
1. 止损：现价较成本亏损达到 STOP_LOSS_PCT（默认 -8%）
2. 止盈：现价较成本盈利达到 TAKE_PROFIT_PCT（默认 +25%）
3. 移动止损：自持仓以来最高价回落 TRAILING_STOP_PCT（默认 0=关闭）

命中后上层（signals / report）将动作定为「必须卖出」，覆盖综合分阈值。
模拟盘会自动卖出；真实持仓仅 advisory（must-sell 提示），不接券商。

注：死叉卖出由技术信号（signals.recommend 的「卖出」）负责，不在本模块重复处理。
"""
from __future__ import annotations

import config


def exit_price(avg_cost: float) -> dict[str, float]:
    """给定成本价，返回止损价/止盈价。"""
    return {
        "stop_loss": round(avg_cost * (1 + config.STOP_LOSS_PCT), 2),
        "take_profit": round(avg_cost * (1 + config.TAKE_PROFIT_PCT), 2),
    }


def check_exit(avg_cost: float, price: float, peak: float | None = None) -> list[str]:
    """返回触发卖出的原因列表，未触发返回空列表。"""
    triggers: list[str] = []
    if not avg_cost or not price:
        return triggers
    pnl = (price - avg_cost) / avg_cost

    if pnl <= config.STOP_LOSS_PCT:
        triggers.append(f"止损：现价较成本{pnl:+.1%}，跌破{config.STOP_LOSS_PCT:+.0%}线")
    if pnl >= config.TAKE_PROFIT_PCT:
        triggers.append(f"止盈：现价较成本{pnl:+.1%}，达到{config.TAKE_PROFIT_PCT:+.0%}线")
    if peak and config.TRAILING_STOP_PCT > 0:
        dd = (price - peak) / peak
        if dd <= -config.TRAILING_STOP_PCT:
            triggers.append(f"移动止损：自高点{peak:.2f}回落{dd:+.1%}")

    return triggers


def must_sell(avg_cost: float, price: float, peak: float | None = None) -> bool:
    """是否应强制卖出（止损/止盈/移动止损任一命中）。"""
    return bool(check_exit(avg_cost, price, peak))
