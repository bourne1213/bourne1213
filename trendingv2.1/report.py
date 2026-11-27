# -*- coding: utf-8 -*-
"""
每日报告生成：把「新闻/时政 + 技术面 + 全市场选股」汇总成结构化结果，
供命令行（runner.py）和网页（app.py）复用，并落地缓存到 report.json。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import config
import data
import news
import llm
import sentiment
import signals
import screener
import factors
import tsdata
import paper
import watchlist
import holdings as holdings_mod

REPORT_FILE = Path(__file__).parent / "report.json"

# 报告生成进度（供网页进度条轮询 /api/progress）
_progress: dict = {"stage": "", "detail": "", "percent": 0, "done": False}


def set_progress(stage: str, detail: str = "", percent: int = 0, done: bool = False) -> None:
    _progress.update(stage=stage, detail=detail, percent=percent, done=done)


def get_progress() -> dict:
    return dict(_progress)

def _next_weekday(date_str: str) -> str:
    """返回 date_str 之后的下一个工作日（跳过周末；不含节假日）。"""
    import pandas as pd
    d = pd.Timestamp(date_str) + pd.Timedelta(days=1)
    while d.dayofweek >= 5:
        d += pd.Timedelta(days=1)
    return str(d.date())


def _norm_symbol(symbol: str) -> str:
    s = str(symbol).strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p) and len(s) == 8:
            return s[2:]
    return s


def _stock_sent_from_llm(llm_res: dict | None, symbol: str) -> float | None:
    if not llm_res:
        return None
    s = llm_res.get("stocks", {}).get(symbol)
    if isinstance(s, dict):
        try:
            return float(s.get("sentiment"))
        except (TypeError, ValueError):
            return None
    return None


def _sector_sent_from_llm(llm_res: dict | None, sector: str) -> float | None:
    if not llm_res or not sector:
        return None
    for t in llm_res.get("themes", []):
        theme = str(t.get("theme", ""))
        if theme and (sector in theme or theme in sector):
            try:
                return float(t.get("sentiment"))
            except (TypeError, ValueError):
                return None
    return None


def _market_view(llm_res: dict | None, macro_texts: list[str]) -> str:
    if llm_res:
        s = llm_res.get("sentiment", 0)
        mood = "偏多" if s > 0.15 else "偏空" if s < -0.15 else "中性"
        lines = [f"市场情绪: {mood} ({s:+.2f})  [大模型]"]
        if llm_res.get("summary"):
            lines.append(f"点评: {llm_res['summary']}")
        for t in llm_res.get("themes", [])[:6]:
            lines.append(f"  板块 {t.get('theme')}: {t.get('sentiment', 0):+.2f} {t.get('reason', '')}")
        return "\n".join(lines)
    impacts = sentiment.sector_impacts(macro_texts)
    if not impacts:
        return "市场情绪: 中性（今日时政未命中预设政策关键词）"
    hot = sorted(impacts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return "市场情绪(词典): " + ", ".join(f"{k}{v:+.2f}" for k, v in hot)


def _apply_constraints(picks: list[dict], top_n: int = 10) -> list[dict]:
    """固定风控约束：绝对门槛、重大负面、过热过滤和行业上限。

    不足 top_n 时不回填弱股票；没有足够合格标的时宁可少推荐。
    """
    # 重大负面新闻直接剔除
    kept = [p for p in picks
            if p.get("news_score", 0.0) > -0.5
            and p.get("final_score", 0.0) >= config.SCREEN_MIN_FINAL_SCORE
            and p.get("ret5_pct", 0.0) <= config.SCREEN_MAX_RET5_PCT
            and p.get("ranks", {}).get("trend", 0.0) >= 0.45
            and p.get("ranks", {}).get("rs", 0.0) >= 0.45]
    kept.sort(key=lambda p: p.get("final_score", 0), reverse=True)
    # 单行业最多2只
    result: list[dict] = []
    sector_count: dict[str, int] = {}
    for p in kept:
        sec = p.get("sector") or "其他"
        if sector_count.get(sec, 0) >= 2:
            continue
        else:
            sector_count[sec] = sector_count.get(sec, 0) + 1
            result.append(p)
    return result[:top_n]


def _holding_lookup(items: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for it in items:
        sym = _norm_symbol(it.get("symbol", ""))
        if sym:
            out[sym] = it
    return out


def build_daily_report(
    stocks: dict[str, str],
    use_demo: bool = False,
    refresh: bool = False,
    do_screener: bool = True,
    trade: bool | None = None,
    save: bool = True,
    need_calendar: bool = True,
) -> dict:
    """生成每日报告，返回结构化 dict；save=True 时写入 report.json。

    trade=None 时使用 config.REPORT_TRADE_DEFAULT（默认 True，含模拟盘风控出场）。
    真实持仓仅产出「必须卖出」建议，不会自动减持 holdings.json。
    """
    if trade is None:
        trade = bool(config.REPORT_TRADE_DEFAULT)

    # Pick up dashboard / providers.json changes without restarting the process
    try:
        import settings_store
        settings_store.apply()
    except Exception:  # noqa: BLE001
        pass

    set_progress("新闻抓取", "抓取 CCTV/财联社/个股新闻…", 3)
    holding_items = holdings_mod.load()
    holding_map = _holding_lookup(holding_items)

    # 真实持仓若不在自选股里，也纳入信号/风控扫描（advisory）
    stocks = dict(stocks)
    for sym, h in holding_map.items():
        if sym not in stocks:
            stocks[sym] = str(h.get("name") or sym)

    if use_demo:
        news_data = {"macro": [], "stocks": {s: [] for s in stocks}}
    else:
        news_data = news.fetch_all(stocks)
    news_status = news.get_last_status() if not use_demo else None
    news_fetch_ok = bool(news_data.get("macro")) or any(news_data.get("stocks", {}).values())
    macro_texts = [n["content"] for n in news_data.get("macro", [])]
    stock_texts = {s: [n["content"] for n in news_data.get("stocks", {}).get(s, [])] for s in stocks}

    set_progress("大模型分析", "分析新闻情绪…", 13)
    llm_res = llm.analyze_news(macro_texts, stock_texts)
    llm_status = llm.get_last_status()
    market_view = _market_view(llm_res, macro_texts)


    sector_map = watchlist.sector_map()

    set_progress("自选股行情", f"共 {len(stocks)} 只", 18)
    recs: list[dict] = []
    prices: dict[str, float] = {}
    data_dates: list[str] = []
    holdings_risk: list[dict] = []
    acct = paper.PaperAccount()
    for i, (symbol, name) in enumerate(stocks.items(), 1):
        set_progress("自选股行情", f"{name} ({i}/{len(stocks)})", 18 + int(7 * i / max(len(stocks), 1)))
        try:
            df = data.generate_demo(symbol) if use_demo else data.load_or_fetch(symbol, refresh=refresh)
        except Exception as exc:  # noqa: BLE001
            recs.append({"symbol": symbol, "name": name, "error": str(exc)})
            continue
        sig_date = data.last_data_date(df)
        data_dates.append(sig_date)
        sector = sector_map.get(symbol, config.STOCK_SECTOR.get(name, "综合"))
        ss = _stock_sent_from_llm(llm_res, symbol)
        cs = _sector_sent_from_llm(llm_res, sector)

        paper_pos = acct.positions.get(symbol)
        real_h = holding_map.get(_norm_symbol(symbol))
        paper_cost = None
        real_cost = None
        peak = None
        is_holding = False
        if paper_pos and paper_pos.get("shares", 0) > 0:
            paper_cost = float(paper_pos.get("avg_cost") or 0) or None
            peak = paper_pos.get("peak")
            is_holding = True
        if real_h:
            real_cost = float(real_h.get("cost_price") or 0) or None
            is_holding = True
        # 信号层优先用真实持仓成本做 must-sell；模拟盘另由 apply_risk_exits 处理
        avg_cost = real_cost or paper_cost

        rec = signals.recommend(
            symbol, name, df, stock_texts.get(symbol, []), macro_texts,
            stock_sent=ss, sector_sent=cs, sector=sector,
            avg_cost=avg_cost, peak=peak, is_holding=is_holding,
        )
        rec["signal_date"] = sig_date
        if real_h:
            rec["real_holding"] = {
                "shares": real_h.get("shares"),
                "cost_price": real_h.get("cost_price"),
            }
            if rec.get("must_sell") or rec["action"] in signals.SELL_ACTIONS:
                note = (
                    f"真实持仓 {name}({symbol}) → {rec['action']} "
                    f"（成本 {real_h.get('cost_price')}，现价 {rec['detail'].get('close')}）"
                )
                rec.setdefault("risk_notes", []).append(note)
                holdings_risk.append({
                    "symbol": symbol,
                    "name": name,
                    "action": rec["action"],
                    "must_sell": bool(rec.get("must_sell")),
                    "shares": real_h.get("shares"),
                    "cost_price": real_h.get("cost_price"),
                    "close": rec["detail"].get("close"),
                    "triggers": rec["detail"].get("risk_triggers") or [],
                    "advisory_only": True,
                })
        recs.append(rec)
        close = rec["detail"]["close"]
        if close is not None:
            prices[symbol] = close

        # 模拟盘：先风控出场，再按建议交易（必须卖出/卖出都会卖）
        if trade and config.PAPER_TRADING:
            px = close
            if px is not None:
                for note in acct.apply_risk_exits(symbol, name, px):
                    rec.setdefault("risk_notes", []).append(note)
                # 若风控已卖空仓，跳过二次卖出噪音
                still_held = symbol in acct.positions
                if still_held or rec["action"] == "买入":
                    for note in acct.trade(rec, px, sig_date):
                        rec.setdefault("trade_notes", []).append(note)
                elif rec["action"] in signals.SELL_ACTIONS and not still_held:
                    rec.setdefault("trade_notes", []).append(f"模拟盘已因风控清仓 {name}")

    if trade and config.PAPER_TRADING:
        acct.save()
        as_of = max([d for d in data_dates if d], default=datetime.now().strftime("%Y-%m-%d"))
        equity = acct.append_equity(prices, as_of=as_of)
    else:
        equity = acct.equity(prices)

    data_fetch_ok = all("error" not in r for r in recs)

    picks: list[dict] = []
    screen_stats: dict = {}
    news_scoring_failed = False
    screen_failed = False
    screen_error: str | None = None
    if do_screener and config.SCREEN_ENABLED and not use_demo:
        try:
            set_progress("全市场选股", "扫描全市场…（最慢，约1-3分钟）", 25)

            def _scan_progress(done: int, total: int) -> None:
                set_progress("全市场选股", f"已扫描 {done}/{total} 只",
                             25 + int(63 * done / max(total, 1)))

            result = screener.scan(progress_cb=_scan_progress)
            screen_stats = result.get("market_stats", {})
            scanned = int(screen_stats.get("scanned", 0) or 0)
            succeeded = int(screen_stats.get("success", 0) or 0)
            minimum_success = max(config.SCREEN_TOP_N * 2, int(scanned * 0.5))
            if succeeded < minimum_success:
                raise RuntimeError(
                    f"有效股票样本不足：成功 {succeeded}/{scanned}，至少需要 {minimum_success}"
                )
            cands = result["candidates"]

            # 1) LLM 只返回 {symbol, news_score, reason}，主数据用 cands 里的数据源字段
            set_progress("新闻打分", "大模型给候选股打分…", 88)
            news_map: dict[str, dict] = {}
            regime = result.get("market_regime", "neutral")
            max_picks = (config.SCREEN_RISK_MAX_PICKS if regime == "risk" else
                         config.SCREEN_NEUTRAL_MAX_PICKS if regime == "neutral" else
                         config.SCREEN_TOP_N)
            refined = llm.finalize_picks(cands, macro_texts, max_picks)
            news_scoring_failed = refined is None
            if refined:
                for r in refined:
                    news_map[r["symbol"]] = {"news_score": r.get("news_score", 0.0),
                                             "reason": r.get("reason", "")}

            # 2) 最终合成：7 技术因子 + 新闻情绪，用全池横截面排名
            ranks_dict = {c["symbol"]: c.get("ranks", {}) for c in cands}
            news_scores = {c["symbol"]: news_map.get(c["symbol"], {}).get("news_score", 0.0) for c in cands}
            final_scores = factors.composite_score(ranks_dict, news_scores)

            picks = []
            for c in cands:
                sym = c["symbol"]
                c["news_score"] = round(news_map.get(sym, {}).get("news_score", 0.0), 3)
                c["reason"] = news_map.get(sym, {}).get("reason", "")
                c["final_score"] = round(final_scores.get(sym, 0.0), 4)
                picks.append(c)

            # 3) 约束：负面新闻禁止、5日暴涨扣分、单行业最多2只（不足回填）
            picks = _apply_constraints(picks, max_picks)
            screen_stats["market_regime"] = regime
            screen_stats["max_picks"] = max_picks
        except Exception as exc:  # noqa: BLE001
            picks = [{"error": f"选股失败: {type(exc).__name__} {str(exc)[:80]}"}]
            screen_failed = True
            screen_error = f"{type(exc).__name__}: {str(exc)[:160]}"

    # 数据日期：自选股数据的最晚日期（展示用）+ 逐只新鲜度检查
    set_progress("生成报告", "汇总结果…", 96)
    market_data_date = max([d for d in data_dates if d]) if data_dates else ""
    calendar_ok = False
    target_trade_date = ""
    from datetime import date as _date
    today = _date.today()
    if need_calendar and market_data_date:
        try:
            target_trade_date = tsdata.next_trade_date(market_data_date)  # 交易日历
            # 收盘后生成的报告不能再把已过去的交易日标成“下一交易日”。
            if target_trade_date <= today.isoformat():
                target_trade_date = tsdata.next_trade_date(today.isoformat())
            calendar_ok = True
        except Exception:  # noqa: BLE001
            target_trade_date = _next_weekday(market_data_date)
    stale_count = 0
    for d in data_dates:
        if not d:
            stale_count += 1
            continue
        try:
            if (today - _date.fromisoformat(d)).days > 3:
                stale_count += 1
        except Exception:  # noqa: BLE001
            stale_count += 1
    data_fresh = bool(data_dates) and stale_count == 0

    # 按板块分组
    picks_by_sector: dict[str, list[dict]] = {}
    for p in picks:
        if p.get("error"):
            continue
        sec = p.get("sector") or "其他"
        picks_by_sector.setdefault(sec, []).append(p)

    # Provider snapshot for dashboard status chips
    from providers import market as market_providers
    from providers.base import provider_snapshot

    market_prov_status = market_providers.get_last_status()
    providers_block = provider_snapshot(
        market=market_prov_status,
        llm=llm_status,
        news=news_status,
    )

    llm_fallback = bool(llm_status and llm_status.fallback)
    report = {
        "project": config.PROJECT_NAME,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "market_data_date": market_data_date,
        "signal_date": market_data_date,
        "target_trade_date": target_trade_date,
        "data_fresh": data_fresh,
        "stale_stock_count": stale_count,
        "llm_enabled": bool(llm_res),
        "llm_fallback": llm_fallback or (not bool(llm_res)),
        "model": config.LLM_MODEL if llm_res else None,
        "market_view": market_view,
        "recs": recs,
        "holdings_risk": holdings_risk,
        "picks": picks,
        "picks_by_sector": picks_by_sector,
        "news_scoring": {"ok": not news_scoring_failed,
                          "error": llm.finalize_error if news_scoring_failed else None},
        "data_quality": {"calendar_ok": calendar_ok, "screen": screen_stats,
                          "screen_error": screen_error},
        "providers": providers_block,
        "status": {
            "llm_ok": bool(llm_res),
            "llm_provider": (llm_status.provider if llm_status else ""),
            "llm_detail": (llm_status.detail if llm_status else ""),
            "llm_fallback": (llm_status.fallback if llm_status else "") or (
                "dictionary" if not llm_res else ""
            ),
            "model": config.LLM_MODEL if llm_res else None,
            "news_fetch_ok": news_fetch_ok,
            "news_provider": (news_status.provider if news_status else ""),
            "news_detail": (news_status.detail if news_status else ""),
            # 全市场扫描若在候选股生成前失败，不应误报为新闻打分失败。
            "news_scoring_ok": not news_scoring_failed,
            "data_ok": data_fetch_ok,
            "market_provider": market_prov_status.provider,
            "market_detail": market_prov_status.detail,
            "calendar_ok": calendar_ok,
            "screen_ok": (not do_screener or not config.SCREEN_ENABLED or
                          (not screen_failed and bool(screen_stats and
                           screen_stats.get("success_rate", 0) >= 0.5))),
            "trade": bool(trade and config.PAPER_TRADING),
        },
        "paper": {
            "cash": acct.cash,
            "positions": acct.positions,
            "equity": equity,
            "summary": acct.summary(prices),
        },
        "thresholds": {
            "buy": config.BUY_THRESHOLD,
            "sell": config.SELL_THRESHOLD,
            "holding_sell": config.HOLDING_SELL_THRESHOLD,
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "take_profit_pct": config.TAKE_PROFIT_PCT,
        },
    }
    if save:
        try:
            tmp = REPORT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, REPORT_FILE)
        except Exception:  # noqa: BLE001
            pass
    set_progress("完成", "", 100, done=True)
    return report


def load_cached_report() -> dict | None:
    if REPORT_FILE.exists():
        try:
            return json.loads(REPORT_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None
