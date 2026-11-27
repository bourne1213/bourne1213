# -*- coding: utf-8 -*-
"""
trendingv2.1 本地网页应用：每日策略看板。

启动：python app.py  → 浏览器打开 http://127.0.0.1:5000

功能：
- 查看今日市场情绪 / 自选股买卖信号 / 全市场 Top 推荐
- 输入真实持仓，实时统计盈亏（止损/止盈仅 advisory「必须卖出」，不接券商）
"""
from __future__ import annotations

import math
import re
import sys
import threading
import time
from pathlib import Path

# 项目内 vendor 若含 flask，优先使用，免去全局安装
_vendor = Path(__file__).parent / "vendor"
if _vendor.exists():
    sys.path.insert(0, str(_vendor))

from flask import Flask, jsonify, request, render_template

import config
import report
import holdings
import watchlist
import screener
import risk
import settings_store
import llm

# 保护「读-改-写」操作，避免多标签页并发互相覆盖
_io_lock = threading.Lock()
_SYMBOL_RE = re.compile(r"^(?:(?:sh|sz|bj)\d{6}|\d{6})$", re.IGNORECASE)


def _valid_stock_items(items) -> tuple[list[dict] | None, str | None]:
    """校验持仓/自选股列表 schema，返回 (清洗后列表, 错误)。"""
    if not isinstance(items, list):
        return None, "数据格式错误（应为数组）"
    cleaned = []
    for it in items:
        if not isinstance(it, dict):
            return None, "每项应为对象"
        symbol = str(it.get("symbol", "")).strip()
        name = str(it.get("name", "")).strip()
        if not _SYMBOL_RE.fullmatch(symbol):
            return None, f"股票代码格式无效: {symbol or '空'}"
        symbol = symbol.lower()
        if not name:
            name = symbol
        cleaned.append({**it, "symbol": symbol, "name": name})
    return cleaned, None


def _valid_holding_item(item) -> tuple[dict | None, str | None]:
    cleaned, err = _valid_stock_items([item])
    if err:
        return None, err
    result = cleaned[0]
    try:
        shares = float(result.get("shares"))
        cost = float(result.get("cost_price"))
    except (TypeError, ValueError):
        return None, f"{result.get('symbol')} 股数/成本价无效"
    if not (math.isfinite(shares) and math.isfinite(cost)) or shares <= 0 or cost <= 0:
        return None, f"{result.get('symbol')} 股数/成本价必须为正的有限数"
    if shares != int(shares):
        return None, f"{result.get('symbol')} 股数必须为整数"
    result["shares"] = int(shares)
    result["cost_price"] = cost
    return result, None

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # 非调试模式也每次重读模板，避免旧模板缓存


@app.after_request
def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp

# 全市场快照缓存（60 秒内复用）：{代码: {name, price}}
_snap_cache: dict = {"ts": 0.0, "data": None}


def _get_snap_map() -> dict:
    now = time.time()
    if _snap_cache["data"] is not None and now - _snap_cache["ts"] < 60:
        return _snap_cache["data"]
    snap = screener.get_snapshot()
    m = {}
    for _, r in snap.iterrows():
        code = str(r["代码"])
        m[code] = {"name": str(r["名称"]), "price": float(r["最新价"])}
    _snap_cache["ts"] = now
    _snap_cache["data"] = m
    return m


def _lookup(snap_map: dict, symbol: str) -> dict | None:
    symbol = str(symbol).strip()
    if symbol in snap_map:
        return snap_map[symbol]
    for p in ("sh", "sz", "bj"):
        if p + symbol in snap_map:
            return snap_map[p + symbol]
    return None


@app.route("/api/stock_info")
def api_stock_info():
    """按代码查名称/板块/最新价。"""
    symbol = str(request.args.get("symbol", "")).strip()
    if not symbol:
        return jsonify({"ok": False, "error": "缺少代码"})
    snap = _get_snap_map()
    hit = _lookup(snap, symbol)
    if hit is None:
        return jsonify({"ok": False, "error": f"未找到 {symbol}"})
    sector = screener.guess_sector(hit["name"])  # 关键词猜测（精确行业源已弃用）
    return jsonify({"ok": True, "symbol": symbol, "name": hit["name"],
                    "sector": sector, "price": hit["price"]})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/providers", methods=["GET", "POST"])
def api_providers():
    """Read / update pluggable provider settings (dashboard Settings panel)."""
    if request.method == "GET":
        return jsonify(settings_store.public_view())

    patch = request.get_json(force=True, silent=True)
    if not isinstance(patch, dict):
        return jsonify({"ok": False, "error": "数据格式错误（应为对象）"}), 400
    allowed = set(settings_store.OVERRIDABLE)
    unknown = [k for k in patch if k not in allowed and k != "reset"]
    if unknown:
        return jsonify({"ok": False, "error": f"未知字段: {', '.join(unknown)}"}), 400

    with _io_lock:
        if patch.get("reset") is True:
            settings_store.save_raw({})
            settings_store.apply({})
        else:
            settings_store.merge_and_save({k: v for k, v in patch.items() if k != "reset"})
    view = settings_store.public_view()
    view["saved"] = True
    view["message"] = "已保存。点「刷新今日分析」即可按新供应商取数（无需重启）。"
    return jsonify(view)


@app.route("/api/providers/test", methods=["POST"])
def api_providers_test():
    """Quick connectivity / resolution check for the currently applied providers."""
    settings_store.apply()
    body = request.get_json(force=True, silent=True) or {}
    target = str(body.get("target", "all")).lower()
    results: dict = {}

    if target in ("all", "llm"):
        ok, msg = llm.test_connection()
        results["llm"] = {
            "ok": ok,
            "detail": msg,
            "provider": settings_store.public_view()["active"]["llm_resolved"],
            "model": config.LLM_MODEL,
        }

    if target in ("all", "market"):
        from providers import market as market_providers
        try:
            chain = [
                {"id": key, "label": label}
                for _, label, key in market_providers.resolve_chain()
            ]
            results["market"] = {
                "ok": bool(chain),
                "detail": "将尝试: " + " → ".join(c["label"] for c in chain) if chain else "无可用源",
                "chain": chain,
                "provider": config.MARKET_PROVIDER,
            }
        except Exception as exc:  # noqa: BLE001
            results["market"] = {"ok": False, "detail": str(exc)[:160]}

    if target in ("all", "news"):
        from providers import news_providers
        enabled = news_providers.parse_enabled_sources()
        results["news"] = {
            "ok": True,
            "detail": ("已选: " + ",".join(enabled)) if enabled else "新闻源已关闭",
            "enabled": enabled,
            "sources": config.NEWS_SOURCES,
        }

    return jsonify({"ok": True, "results": results, "active": settings_store.public_view()["active"]})


@app.route("/api/report")
def api_report():
    refresh = request.args.get("refresh") == "1"
    # Always re-apply local providers.json so Settings panel changes take effect
    settings_store.apply()
    cached = report.load_cached_report()
    if not refresh and cached:
        # 缓存日期校验：仅当天生成的缓存才直接返回，跨天自动重建
        gen_date = str(cached.get("generated_at", ""))[:10]
        if gen_date == time.strftime("%Y-%m-%d"):
            return jsonify(cached)
    rep = report.build_daily_report(
        watchlist.as_dict(),
        use_demo=False,
        refresh=refresh,
        do_screener=True,
        trade=bool(config.WEB_REPORT_TRADE),
    )
    return jsonify(rep)


@app.route("/api/progress")
def api_progress():
    """报告生成进度（网页进度条轮询）。"""
    return jsonify(report.get_progress())


@app.route("/api/recs")
def api_recs():
    """只重算自选股买卖信号（跳过全市场选股与交易日历，较快且不碰 Tushare），供加/删自选股后即时更新。"""
    # 快速重算自选信号：默认也跑纸面风控出场（与 CLI / 日报对齐，可在 config 关闭）
    rep = report.build_daily_report(
        watchlist.as_dict(), use_demo=False, refresh=False, do_screener=False,
        trade=bool(config.WEB_REPORT_TRADE),
        save=False, need_calendar=False
    )
    return jsonify({"recs": rep["recs"], "market_view": rep["market_view"],
                    "llm_enabled": rep["llm_enabled"], "model": rep["model"]})


@app.route("/api/watchlist", methods=["GET", "POST"])
def api_watchlist():
    if request.method == "POST":
        items = request.get_json(force=True)
        cleaned, err = _valid_stock_items(items)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        with _io_lock:
            watchlist.save(cleaned)
        return jsonify({"ok": True})
    return jsonify(watchlist.load())


@app.route("/api/watchlist/item", methods=["POST", "DELETE"])
def api_watchlist_item():
    """在服务端锁内按股票代码增改或删除，避免整表更新互相覆盖。"""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "数据格式错误（应为对象）"}), 400
    if request.method == "POST":
        cleaned, err = _valid_stock_items([data])
        if err:
            return jsonify({"ok": False, "error": err}), 400
        item = cleaned[0]
        with _io_lock:
            items = watchlist.load()
            idx = next((i for i, old in enumerate(items)
                        if str(old.get("symbol", "")).lower() == item["symbol"]), -1)
            if idx >= 0:
                items[idx] = item
            else:
                items.append(item)
            watchlist.save(items)
        return jsonify({"ok": True, "items": items})

    symbol = str(data.get("symbol", "")).strip().lower()
    if not _SYMBOL_RE.fullmatch(symbol):
        return jsonify({"ok": False, "error": "股票代码格式无效"}), 400
    with _io_lock:
        items = watchlist.load()
        kept = [it for it in items if str(it.get("symbol", "")).lower() != symbol]
        if len(kept) == len(items):
            return jsonify({"ok": False, "error": f"自选股里没有 {symbol}"}), 404
        watchlist.save(kept)
    return jsonify({"ok": True, "items": kept})


@app.route("/api/holdings", methods=["GET", "POST"])
def api_holdings():
    if request.method == "POST":
        items = request.get_json(force=True)
        cleaned, err = _valid_stock_items(items)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        validated = []
        for it in cleaned:
            holding, err = _valid_holding_item(it)
            if err:
                return jsonify({"ok": False, "error": err}), 400
            validated.append(holding)
        with _io_lock:
            holdings.save(validated)
        return jsonify({"ok": True})
    return jsonify(holdings.load())


@app.route("/api/holdings/item", methods=["POST"])
def api_holding_item():
    """在服务端锁内按代码新增或更新单只持仓。"""
    item, err = _valid_holding_item(request.get_json(force=True))
    if err:
        return jsonify({"ok": False, "error": err}), 400
    with _io_lock:
        items = holdings.load()
        idx = next((i for i, old in enumerate(items)
                    if str(old.get("symbol", "")).lower() == item["symbol"]), -1)
        if idx >= 0:
            items[idx] = item
        else:
            items.append(item)
        holdings.save(items)
    return jsonify({"ok": True, "items": items})


@app.route("/api/holdings/sell", methods=["POST"])
def api_holdings_sell():
    """卖出/减持：{symbol, shares}，从持仓中扣除；剩余<=0 则删除。"""
    data = request.get_json(force=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "数据格式错误（应为对象）"}), 400
    symbol = str(data.get("symbol", "")).strip()
    try:
        sell_shares = float(data.get("shares"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "卖出股数无效"}), 400
    if not math.isfinite(sell_shares) or sell_shares <= 0:
        return jsonify({"ok": False, "error": "卖出股数必须为正数"}), 400
    if sell_shares != int(sell_shares):
        return jsonify({"ok": False, "error": "卖出股数必须为整数"}), 400
    sell_shares = int(sell_shares)
    with _io_lock:
        items = holdings.load()
        for i, it in enumerate(items):
            if str(it.get("symbol")) == symbol:
                try:
                    current = float(it.get("shares", 0) or 0)
                except (TypeError, ValueError):
                    current = 0
                if sell_shares > current:
                    return jsonify({"ok": False, "error": f"卖出股数超过持仓 {current} 股"}), 400
                remain = current - sell_shares
                if remain <= 0:
                    items.pop(i)
                else:
                    items[i]["shares"] = remain
                holdings.save(items)
                return jsonify({"ok": True, "sold": sell_shares, "remain": remain, "items": items})
        return jsonify({"ok": False, "error": f"持仓里没有 {symbol}"}), 404


@app.route("/api/pnl")
def api_pnl():
    items = holdings.load()
    if not items:
        return jsonify({"rows": [], "total_cost": 0, "total_value": 0, "total_pnl": 0, "total_pnl_pct": 0})

    snap_map = _get_snap_map()
    rows = []
    total_cost = 0.0
    total_value = 0.0
    unpriced_cost = 0.0
    missing = 0
    for it in items:
        symbol = str(it.get("symbol", "")).strip()
        shares = float(it.get("shares", 0) or 0)
        cost = float(it.get("cost_price", 0) or 0)
        name = it.get("name", symbol)
        position_cost = shares * cost
        total_cost += position_cost  # 总成本始终累计（含无报价持仓）
        hit = _lookup(snap_map, symbol)
        price = hit["price"] if hit else None
        if price is None:
            unpriced_cost += position_cost
            missing += 1
            rows.append({"symbol": symbol, "name": name, "shares": shares, "cost": cost,
                         "price": None, "value": None, "pnl": None, "pnl_pct": None})
            continue
        value = shares * price
        pnl = shares * (price - cost)
        pnl_pct = (price - cost) / cost * 100 if cost else 0.0
        ep = risk.exit_price(cost) if cost else {"stop_loss": 0.0, "take_profit": 0.0}
        triggers = risk.check_exit(cost, price) if cost else []
        trigger = ""
        must_sell = False
        if triggers:
            must_sell = True
            trigger = "必须卖出"
            if any("止损" in t for t in triggers):
                trigger = "必须卖出/止损"
            elif any("止盈" in t for t in triggers):
                trigger = "必须卖出/止盈"
        total_value += value
        rows.append({"symbol": symbol, "name": name, "shares": shares, "cost": round(cost, 3),
                     "price": round(price, 3), "value": round(value, 2),
                     "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                     "stop_loss": ep["stop_loss"], "take_profit": ep["take_profit"],
                     "trigger": trigger, "must_sell": must_sell,
                     "risk_triggers": triggers, "advisory_only": True})

    priced_cost = total_cost - unpriced_cost
    total_pnl = total_value - priced_cost
    total_pnl_pct = total_pnl / priced_cost * 100 if priced_cost else 0.0
    return jsonify({
        "rows": rows,
        "total_cost": round(total_cost, 2),
        "priced_cost": round(priced_cost, 2),
        "unpriced_cost": round(unpriced_cost, 2),
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "missing_quotes": missing,
        "partial": missing > 0,
    })


if __name__ == "__main__":
    print("=" * 46)
    print(f"{config.PROJECT_TITLE} 已启动")
    print("项目： trendingv2.1")
    print("浏览器打开： http://127.0.0.1:5000")
    print("按 Ctrl+C 退出")
    print("=" * 46)
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
