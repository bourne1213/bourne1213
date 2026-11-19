# -*- coding: utf-8 -*-
"""
trendingv2.1 入口。

用法示例：
    # 每日策略：新闻/时政(大模型或词典) + 全市场选股 + 模拟盘 -> 推送
    python runner.py --daily

    # 只看自选股、不跑全市场选股
    python runner.py --daily --no-screener

    # 纯技术信号（不抓新闻，向后兼容）
    python runner.py

    # 回测 + 最新信号
    python runner.py --symbol 600519 --backtest

    # 测试大模型连接
    python runner.py --test-llm

    # 网页看板
    python app.py
"""
from __future__ import annotations

import argparse
import sys
import time

import config
import data
import strategy
import backtest
import notify
import signals
import report
import llm
import watchlist


def run_symbol(symbol: str, name: str, use_demo: bool, do_backtest: bool, refresh: bool) -> None:
    """纯技术面：回测 + 均线信号（向后兼容）。"""
    if use_demo:
        df = data.generate_demo(symbol)
    else:
        try:
            df = data.load_or_fetch(symbol, refresh=refresh)
        except Exception as exc:
            notify.send(f"[{name}] 取数失败", str(exc))
            return

    if do_backtest:
        res = backtest.run_backtest(df)
        print(f"== {name}({symbol}) ==")
        print(backtest.format_report(res))

    info = strategy.latest_signal(df)
    if info and info["signal"] != 0:
        title = f"[{name}] {strategy.SIGNAL_TEXT[info['signal']]}信号"
        notify.send(title, strategy.describe(info))
    elif info:
        print(f"[{name}] 最新: {strategy.describe(info)}")


def _format_picks_lines(rep: dict) -> list[str]:
    """把 Top N 推荐格式化成多行文本（推送与打印共用）。"""
    picks = rep.get("picks") or []
    if not picks:
        return []
    lines = [f"【推荐买入 Top {len(picks)}】"
             f"（数据日 {rep.get('signal_date','')}，下一交易日 {rep.get('target_trade_date','')}）"]
    for i, p in enumerate(picks, 1):
        if p.get("error"):
            lines.append(p["error"])
            continue
        tag = f"评分 {p.get('final_score', p.get('tech_score', ''))}"
        lines.append(f"{i}. {p['name']}({p['symbol']}) [{p.get('sector', '')}] 收盘{p.get('close')} | "
                     f"5日{p.get('ret5_pct')}% | 成交{p.get('amount_yi')}亿 | {tag}")
        if p.get("reason"):
            lines.append(f"   理由: {p['reason']}")
    return lines


def _format_report_text(rep: dict) -> str:
    parts = [rep["market_view"], ""]
    for r in rep["recs"]:
        if r.get("error"):
            parts.append(f"{r.get('name', r.get('symbol'))}: {r['error']}")
            continue
        parts.append(signals.format_recommendation(r))
        for n in r.get("risk_notes", []):
            parts.append(f"  ⚠️ {n}")
        for n in r.get("trade_notes", []):
            parts.append(f"  模拟盘: {n}")
    picks_lines = _format_picks_lines(rep)
    if picks_lines:
        parts.append("")
        parts += picks_lines
    parts.append("")
    parts.append(rep["paper"]["summary"])
    return "\n".join(parts)


def run_daily(stocks: dict[str, str], use_demo: bool, refresh: bool, do_screener: bool) -> None:
    rep = report.build_daily_report(
        stocks, use_demo=use_demo, refresh=refresh, do_screener=do_screener,
        trade=bool(config.REPORT_TRADE_DEFAULT and config.PAPER_TRADING),
    )
    if rep["llm_enabled"]:
        print(f"✅ 大模型语义分析已启用（{config.LLM_MODEL}）")
    else:
        print("⚠️ 大模型未启用/调用失败，本次使用词典法")
    print("\n" + "=" * 40)
    print(rep["market_view"])
    print("=" * 40 + "\n")

    for r in rep["recs"]:
        if r.get("error"):
            print(f"[{r.get('name', r.get('symbol'))}] {r['error']}")
            continue
        print(signals.format_recommendation(r))
        for n in r.get("risk_notes", []):
            print(f"  ⚠️ {n}")
        for n in r.get("trade_notes", []):
            print(f"  模拟盘: {n}")
        print()

    hr = rep.get("holdings_risk") or []
    if hr:
        print("【真实持仓风控（仅建议，不自动下单）】")
        for h in hr:
            print(f"  {h['name']}({h['symbol']}) → {h['action']} "
                  f"成本{h.get('cost_price')} 现价{h.get('close')}")
            for t in h.get("triggers") or []:
                print(f"    - {t}")
        print()

    for line in _format_picks_lines(rep):
        print(line)

    print("\n")
    notify.send(f"{config.PROJECT_NAME} 每日策略报告", _format_report_text(rep))


def main() -> None:
    # Windows 控制台默认 GBK，打印 ✅/⚠️ 等 emoji 会抛 UnicodeEncodeError；
    # 统一改为 UTF-8，并把无法编码的字符替换为占位符而非崩溃。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(
        description=f"{config.PROJECT_NAME}：技术面 + 消息面 + 全市场选股 + 模拟盘（无券商实盘）"
    )
    parser.add_argument("--symbol", default=None, help="单只股票代码，如 600519")
    parser.add_argument("--daily", action="store_true", help="每日策略模式：新闻+时政+全市场选股+模拟盘")
    parser.add_argument("--no-screener", action="store_true", help="跳过全市场选股（只看自选股）")
    parser.add_argument("--demo", action="store_true", help="离线合成行情（无需网络）")
    parser.add_argument("--backtest", action="store_true", help="输出回测报告")
    parser.add_argument("--refresh", action="store_true", help="忽略本地缓存，重新拉行情")
    parser.add_argument("--loop", action="store_true", help="循环运行")
    parser.add_argument("--interval", type=int, default=3600, help="循环间隔秒数（配合 --loop）")
    parser.add_argument("--test-llm", action="store_true", help="测试大模型连接是否成功")
    parser.add_argument("--portfolio", action="store_true", help="运行 Top 10 组合回测")
    args = parser.parse_args()

    if args.test_llm:
        ok, msg = llm.test_connection()
        print(("✅ " if ok else "❌ ") + msg)
        return

    if args.portfolio:
        import portfolio_backtest
        res = portfolio_backtest.run()
        print(portfolio_backtest.format_report(res))
        return

    if args.symbol:
        stocks = {args.symbol: config.STOCKS.get(args.symbol, args.symbol)}
    else:
        stocks = watchlist.as_dict()

    while True:
        if args.daily:
            run_daily(stocks, args.demo, args.refresh, not args.no_screener)
        else:
            for symbol, name in stocks.items():
                run_symbol(symbol, name, args.demo, args.backtest, args.refresh)

        if not args.loop:
            break
        args.refresh = True
        print(f"\n{time.strftime('%H:%M:%S')} 本轮结束，{args.interval}s 后再次检查…")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
