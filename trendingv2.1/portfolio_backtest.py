# -*- coding: utf-8 -*-
"""
第三阶段：Top 10 组合滚动回测（独立于单股双均线回测）。

流程：
- 固定股票池（当前流动性 top N，含幸存者偏差，见 README 说明）
- 每个调仓日 T（每 rebalance_days 个交易日）：
    只用 T 日及之前的数据计算因子 -> 横截面排名 -> 取 top_n
- T+1 开盘等权买入（每只 1/top_n 权重），卖出不在新名单的持仓
- 计入佣金（最低5元）、卖出印花税、滑点
- 与沪深300 / 中证500 对比

输出指标：年化/累计收益、最大回撤、换手率、超额收益、Rank IC、
top_n 的次日/5日/20日平均收益、行业暴露、不同阶段表现。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
import factors
import screener
from costs import commission, stamp_tax


def _fetch_universe(size: int) -> list[tuple[str, str]]:
    snap = screener.get_snapshot()
    uni = screener.filter_universe(snap)
    uni = uni.sort_values("成交额", ascending=False).head(size)
    return [(str(r["代码"]), str(r["名称"])) for _, r in uni.iterrows()]


def _fetch_benchmarks() -> tuple[pd.DataFrame, pd.DataFrame]:
    """沪深300、中证500 指数（新浪）。"""
    import akshare as ak
    screener._apply_proxy()
    out = []
    for sym in ("sh000300", "sh000905"):
        raw = ak.stock_zh_index_daily(symbol=sym)
        df = pd.DataFrame({"date": pd.to_datetime(raw["date"]), "close": raw["close"].astype(float)})
        out.append(df.sort_values("date").reset_index(drop=True))
    return out[0], out[1]


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank()))


def run(
    universe_size: int = 150,
    top_n: int = 10,
    lookback_days: int = 250,
    rebalance_days: int = 5,
    initial_capital: float = config.INITIAL_CAPITAL,
) -> dict:
    """执行组合回测，返回指标字典。"""
    # 1) 股票池 + 历史
    uni = _fetch_universe(universe_size)
    histories: dict[str, pd.DataFrame] = {}
    names: dict[str, str] = {}
    for code, name in uni:
        try:
            df = screener.fetch_hist(code)
            if len(df) >= config.LONG_MA + 40:
                histories[code] = df
                names[code] = name
        except Exception:  # noqa: BLE001
            continue
    if len(histories) < top_n * 2:
        return {"error": "股票池历史数据不足"}

    hs300, zz500 = _fetch_benchmarks()

    # 2) 交易日序列（取所有股票历史日期并集）
    all_dates = sorted(set().union(*[set(df["date"]) for df in histories.values()]))
    dates = pd.to_datetime(all_dates)
    dates = dates[dates >= dates.max() - pd.Timedelta(days=lookback_days * 1.6)]  # 约覆盖回测期
    dates = sorted(dates)

    # 3) 循环：逐交易日盯市，调仓日收盘出信号、次日开盘按股数执行
    cash = initial_capital
    holdings: dict[str, float] = {}  # symbol -> 股数
    pending_target: dict[str, float] | None = None  # 收盘产生的目标权重，次日开盘执行
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    rebalance_set = set(range(0, len(dates), rebalance_days))
    trade_log: list[dict] = []
    rank_ics: list[float] = []
    forward_returns: list[dict] = []
    last_picks: list[str] = []

    # 原始开盘价用于判断当日是否可成交；收盘价向前填充用于停牌日估值。
    opens = {code: dict(zip(df["date"], df["open"])) for code, df in histories.items()}
    date_index = pd.DatetimeIndex(dates)
    mark_prices = {
        code: df.set_index("date")["close"].reindex(date_index).ffill().to_dict()
        for code, df in histories.items()
    }

    def _valuation_price(code: str, date, prefer_open: bool = False) -> float:
        if prefer_open:
            px = opens[code].get(date)
            if px is not None and not pd.isna(px):
                return float(px)
        px = mark_prices[code].get(date)
        return float(px) if px is not None and not pd.isna(px) else 0.0

    def _mark(date) -> float:
        """按收盘价计算当前总权益。"""
        return cash + sum(
            shares * _valuation_price(code, date)
            for code, shares in holdings.items()
        )

    for i, date in enumerate(dates):
        # 1) 开盘执行上个调仓日收盘产生的目标
        if pending_target is not None:
            traded = 0.0
            equity_open = cash + sum(
                shares * _valuation_price(code, date, prefer_open=True)
                for code, shares in holdings.items()
            )

            # 先卖出目标外或超配的部分，释放现金后再买入低配部分。
            for code in list(holdings.keys()):
                px = opens[code].get(date)
                if px is None or pd.isna(px):  # 停牌，保留原仓位并按最近收盘价估值
                    continue
                current_shares = holdings[code]
                target_value = equity_open * pending_target.get(code, 0.0)
                target_shares = target_value / float(px)
                sell_shares = max(current_shares - target_shares, 0.0)
                if sell_shares <= 0:
                    continue
                sell_px = float(px) * (1 - config.SLIPPAGE)
                gross = sell_shares * sell_px
                cash += gross - commission(gross) - stamp_tax(gross)
                traded += gross
                remaining = current_shares - sell_shares
                if remaining <= 1e-12:
                    holdings.pop(code, None)
                else:
                    holdings[code] = remaining

            for code, w in pending_target.items():
                px = opens[code].get(date)
                if px is None or pd.isna(px):
                    continue
                cur_shares = holdings.get(code, 0.0)
                target_val = equity_open * w
                desired_val = max(target_val - cur_shares * float(px), 0.0)
                if desired_val <= 0:
                    continue
                buy_px = float(px) * (1 + config.SLIPPAGE)
                max_notional = max(min(cash - 5.0, cash / (1 + config.COMMISSION_RATE)), 0.0)
                notional = min(desired_val, max_notional)
                if notional <= 0:
                    continue
                buy_shares = notional / buy_px
                cost = notional + commission(notional)
                cash -= cost
                holdings[code] = cur_shares + buy_shares
                traded += notional
            equity = _mark(date)
            trade_log.append({"date": date, "turnover": traded / equity_open if equity_open else 0.0})
            pending_target = None

        # 2) 收盘盯市（每日净值，含当日涨跌）
        equity = _mark(date)
        equity_curve.append((date, equity))

        # 3) 调仓日：收盘计算因子 -> 次日目标 + Rank IC + 未来收益
        if i in rebalance_set:
            bench_slice = hs300[hs300["date"] <= date]
            bench_ret20 = factors.benchmark_ret20(bench_slice)
            raw = {}
            for code, df in histories.items():
                d = df[df["date"] <= date]
                if len(d) < config.LONG_MA + 40:
                    continue
                f = factors.compute_raw_factors(d, bench_ret20)
                if f:
                    raw[code] = f
            if len(raw) < top_n:
                continue
            ranks = factors.cross_section_rank(raw)
            scores = factors.composite_score(ranks, None)
            ranking = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            top_picks = [code for code, _ in ranking[:top_n]]

            future_ret = {}
            for code in raw:
                d = histories[code]
                idx = d.index[d["date"] <= date][-1]
                base_close = d.iloc[idx]["close"]
                fut = d.iloc[idx + 1: idx + 6]
                if len(fut) >= 5:
                    future_ret[code] = fut["close"].iloc[-1] / base_close - 1
            if future_ret:
                s = pd.Series({c: scores[c] for c in future_ret})
                r = pd.Series(future_ret)
                rank_ics.append(_spearman(s, r))

            for horizon, n in (("next1", 1), ("next5", 5), ("next20", 20)):
                rets = []
                for code in top_picks:
                    d = histories[code]
                    idx = d.index[d["date"] <= date][-1]
                    base_close = d.iloc[idx]["close"]
                    fut = d.iloc[idx + 1: idx + 1 + n]
                    if len(fut) >= n:
                        rets.append(fut["close"].iloc[-1] / base_close - 1)
                if rets:
                    forward_returns.append({"horizon": horizon, "avg": float(np.mean(rets))})

            pending_target = {code: 1.0 / top_n for code in top_picks}
            last_picks = top_picks

    if not equity_curve:
        return {"error": "回测区间过短"}

    eq = pd.Series([v for _, v in equity_curve], index=[d for d, _ in equity_curve])
    # 基准同期
    hs = hs300[hs300["date"].isin(eq.index)].set_index("date")["close"]
    zz = zz500[zz500["date"].isin(eq.index)].set_index("date")["close"]

    def _stats(s: pd.Series) -> dict:
        ret = s.iloc[-1] / s.iloc[0] - 1
        n_years = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
        ann = (1 + ret) ** (1 / n_years) - 1
        mdd = float((s / s.cummax() - 1).min())
        return {"total": ret, "annual": ann, "mdd": mdd}

    port_stats = _stats(eq)
    hs_stats = _stats(hs) if len(hs) > 1 else {"total": 0.0, "annual": 0.0, "mdd": 0.0}
    zz_stats = _stats(zz) if len(zz) > 1 else {"total": 0.0, "annual": 0.0, "mdd": 0.0}

    avg_turnover = float(np.mean([t["turnover"] for t in trade_log])) if trade_log else 0.0
    avg_rank_ic = float(np.mean(rank_ics)) if rank_ics else 0.0

    fwd = {}
    for h in ("next1", "next5", "next20"):
        vals = [x["avg"] for x in forward_returns if x["horizon"] == h]
        fwd[h] = float(np.mean(vals)) if vals else 0.0

    # 行业暴露（关键词猜测，取最近一期持仓）
    industry_counts: dict[str, int] = {}
    for code in last_picks:
        sec = screener.guess_sector(names.get(code, ""))
        industry_counts[sec] = industry_counts.get(sec, 0) + 1

    return {
        "portfolio": port_stats,
        "hs300": hs_stats,
        "zz500": zz_stats,
        "excess_vs_hs300": port_stats["total"] - hs_stats["total"],
        "excess_vs_zz500": port_stats["total"] - zz_stats["total"],
        "avg_turnover": round(avg_turnover, 4),
        "avg_rank_ic": round(avg_rank_ic, 4),
        "forward_returns": {k: round(v, 4) for k, v in fwd.items()},
        "industry_exposure": dict(sorted(industry_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]),
        "rebalance_count": len(trade_log),
        "equity": eq,
    }


def format_report(res: dict) -> str:
    if res.get("error"):
        return f"回测失败: {res['error']}"
    p, h, z = res["portfolio"], res["hs300"], res["zz500"]
    lines = [
        "=" * 52,
        "Top 10 组合回测（T+1开盘，每5日调仓）",
        "=" * 52,
        f"组合  累计 {p['total']:+.2%} | 年化 {p['annual']:+.2%} | 最大回撤 {p['mdd']:.2%}",
        f"沪深300 累计 {h['total']:+.2%} | 年化 {h['annual']:+.2%} | 回撤 {h['mdd']:.2%}",
        f"中证500 累计 {z['total']:+.2%} | 年化 {z['annual']:+.2%} | 回撤 {z['mdd']:.2%}",
        f"超额(对沪深300): {res['excess_vs_hs300']:+.2%} | 超额(对中证500): {res['excess_vs_zz500']:+.2%}",
        f"平均换手率: {res['avg_turnover']:.2%} | 平均 Rank IC: {res['avg_rank_ic']:.3f}",
        f"Top10 次日/5日/20日平均收益: {res['forward_returns']['next1']:+.2%} / "
        f"{res['forward_returns']['next5']:+.2%} / {res['forward_returns']['next20']:+.2%}",
        f"调仓次数: {res['rebalance_count']}",
        "近期行业暴露: " + ", ".join(f"{k}x{v}" for k, v in res["industry_exposure"].items()),
        "=" * 52,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Top 10 组合滚动回测")
    parser.add_argument("--universe", type=int, default=150, help="股票池大小")
    parser.add_argument("--top-n", type=int, default=10, help="每次持仓数量")
    parser.add_argument("--lookback", type=int, default=250, help="回测交易日数")
    parser.add_argument("--rebalance", type=int, default=5, help="调仓间隔(交易日)")
    args = parser.parse_args()
    res = run(universe_size=args.universe, top_n=args.top_n,
              lookback_days=args.lookback, rebalance_days=args.rebalance)
    print(format_report(res))
