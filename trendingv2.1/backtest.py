# -*- coding: utf-8 -*-
"""
回测层：双均线策略，消除前视偏差。

成交时点：
- T 日收盘：计算因子与信号
- T+1 日开盘：按开盘价 + 滑点成交
- T+1 日收盘：持仓净值

模拟成本与约束：
- 佣金（最低 5 元，双边）
- 卖出印花税
- 涨跌停无法成交、停牌无法成交
- A股 T+1（次日开盘成交天然满足）
- 期末平仓费用写入最终净值

注：当前使用前复权(qfq)价格计算与成交（收益率近似正确）；绝对价格与真实成交价存在差异，
组合回测(portfolio_backtest)应使用未复权价格成交、复权价格计算因子。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
import strategy
from costs import commission, stamp_tax


@dataclass
class BacktestResult:
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    trades: int = 0
    wins: int = 0
    win_rate: float = 0.0
    equity: pd.Series = field(default_factory=pd.Series)


def run_backtest(df: pd.DataFrame, initial_capital: float = config.INITIAL_CAPITAL) -> BacktestResult:
    ind = strategy.add_indicators(df).reset_index(drop=True)

    cash = initial_capital
    shares = 0
    entry_price = 0.0
    trades = 0
    wins = 0
    pending: str | None = None  # 收盘产生的挂单，次日开盘执行

    dates = []
    values = []

    for i in range(len(ind)):
        row = ind.iloc[i]
        date = row["date"]
        open_ = float(row["open"])
        close = float(row["close"])
        volume = float(row["volume"]) if not pd.isna(row["volume"]) else 0.0

        prev_close = float(ind.iloc[i - 1]["close"]) if i > 0 else open_
        limit_up = round(prev_close * 1.10, 2)
        limit_down = round(prev_close * 0.90, 2)
        suspended = volume <= 0

        # 1) 开盘执行昨日收盘产生的挂单（T+1 开盘成交）
        if pending == "buy" and shares == 0:
            exec_price = open_ * (1 + config.SLIPPAGE)
            # 停牌或开盘涨停无法买入
            if not suspended and open_ < limit_up:
                lots = int(cash // (exec_price * (1 + config.COMMISSION_RATE)) // 100 * 100)
                if lots > 0:
                    cost = lots * exec_price + commission(lots * exec_price)
                    cash -= cost
                    shares = lots
                    entry_price = exec_price
            pending = None
        elif pending == "sell" and shares > 0:
            exec_price = open_ * (1 - config.SLIPPAGE)
            # 停牌或开盘跌停无法卖出
            if not suspended and open_ > limit_down:
                gross = shares * exec_price
                proceeds = gross - commission(gross) - stamp_tax(gross)
                cash += proceeds
                trades += 1
                if exec_price > entry_price:
                    wins += 1
                shares = 0
            pending = None

        # 2) 收盘计算信号（T 日收盘），次日开盘执行
        if not pd.isna(row["ma_long"]):
            sig = int(row["signal"])
            if sig == 1 and shares == 0:
                pending = "buy"
            elif sig == -1 and shares > 0:
                pending = "sell"

        # 3) 收盘净值
        dates.append(date)
        values.append(cash + shares * close)

    # 期末按最后一根K线收盘价平仓，费用写入最终净值
    if shares > 0:
        last = ind.iloc[-1]
        exec_price = float(last["close"]) * (1 - config.SLIPPAGE)
        gross = shares * exec_price
        cash += gross - commission(gross) - stamp_tax(gross)
        trades += 1
        if exec_price > entry_price:
            wins += 1
        shares = 0
        values[-1] = cash  # 期末净值 = 平仓后现金

    equity = pd.Series(values, index=pd.to_datetime(dates))
    start_ts = equity.index[0] - pd.Timedelta(days=1)
    equity = pd.concat([pd.Series([initial_capital], index=[start_ts]), equity])

    total_return = equity.iloc[-1] / initial_capital - 1
    n_years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    annual_return = (1 + total_return) ** (1 / n_years) - 1
    running_max = equity.cummax()
    drawdown = equity / running_max - 1
    max_drawdown = float(drawdown.min())

    return BacktestResult(
        total_return=float(total_return),
        annual_return=float(annual_return),
        max_drawdown=max_drawdown,
        trades=trades,
        wins=wins,
        win_rate=(wins / trades) if trades else 0.0,
        equity=equity,
    )


def format_report(res: BacktestResult) -> str:
    lines = [
        "=" * 46,
        "回测结果（T+1开盘成交）",
        "=" * 46,
        f"总收益率    : {res.total_return:+.2%}",
        f"年化收益率  : {res.annual_return:+.2%}",
        f"最大回撤    : {res.max_drawdown:.2%}",
        f"交易次数    : {res.trades}",
        f"胜率        : {res.win_rate:.2%}",
        "=" * 46,
    ]
    return "\n".join(lines)
