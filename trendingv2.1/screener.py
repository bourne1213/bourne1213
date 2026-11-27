# -*- coding: utf-8 -*-
"""
全市场选股（第二阶段：确定性多因子评分）。

流程：
1. 全市场快照（新浪）过滤：ST/退市/次新、北交所、低流动性、涨跌停
2. 按成交额取前 SCREEN_UNIVERSE_SIZE 只作为评分池
3. 逐只拉历史，计算原始因子（趋势/相对强度/动量/波动/回撤/流动性）
4. 横截面百分位排名 + 加权合成技术分（不含新闻）
5. 返回 top 候选（含因子排名），由 report.py 叠加 LLM 新闻情绪做最终排序与约束
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import config
import data as data_mod
import factors
import tsdata
from sectors import guess_sector

_SCREEN_CACHE = config.DATA_DIR / "screen"

def _apply_proxy() -> None:
    data_mod._apply_proxy_policy()


def _ak_call(ak, *names: str):
    for name in names:
        fn = getattr(ak, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(f"akshare 缺少接口: {' / '.join(names)}")


def get_snapshot() -> pd.DataFrame:
    import json
    import akshare as ak
    _apply_proxy()
    spot = _ak_call(ak, "stock_zh_a_spot_sina", "stock_zh_a_spot_em", "stock_zh_a_spot")
    try:
        snap = spot()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"全市场快照 JSON 解析失败（可能返回 HTML）: {exc}") from exc
    if isinstance(snap, str) or data_mod.looks_like_html(snap):
        data_mod.ensure_json_payload(snap, "全市场快照")
    if snap is None or getattr(snap, "empty", False):
        raise RuntimeError("全市场快照为空")
    return snap



def filter_universe(snap: pd.DataFrame) -> pd.DataFrame:
    df = snap.copy()
    df = df[~df["名称"].astype(str).str.contains("ST|退|N|C", regex=True, na=False)]
    df = df[~df["代码"].astype(str).str.startswith("bj")]
    df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce")
    df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
    df["涨跌幅"] = pd.to_numeric(df["涨跌幅"], errors="coerce")
    df = df[df["成交额"] >= config.SCREEN_MIN_AMOUNT]
    df = df[df["最新价"] >= 2.0]
    df = df[df["涨跌幅"].abs() < 9.5]
    return df


def _cache_path(code: str) -> Path:
    return _SCREEN_CACHE / f"{code}.csv"


def fetch_hist(code: str, last_td: pd.Timestamp | None = None) -> pd.DataFrame:
    """拉单只股票历史（新浪，前复权），最近交易日缓存。"""
    import akshare as ak

    if last_td is None:
        try:
            last_td = pd.Timestamp(tsdata.last_trade_date())
        except Exception:  # noqa: BLE001
            last_td = pd.Timestamp.today().normalize()

    cache = _cache_path(code)
    if cache.exists():
        try:
            df = pd.read_csv(cache, parse_dates=["date"])
            if df["date"].max() >= last_td:
                return df
        except Exception:  # noqa: BLE001
            pass

    _apply_proxy()
    import json
    hist = _ak_call(ak, "stock_zh_a_hist_sina", "stock_zh_a_daily")
    try:
        if hist.__name__ == "stock_zh_a_hist_sina":
            raw = hist(
                symbol=code,
                period="daily",
                start_date=config.START_DATE,
                end_date=pd.Timestamp.today().strftime("%Y%m%d"),
                adjust="qfq",
            )
        else:
            raw = hist(symbol=code, adjust="qfq")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{code} 历史行情 JSON 解析失败: {exc}") from exc
    if isinstance(raw, str) or data_mod.looks_like_html(raw):
        data_mod.ensure_json_payload(raw, f"{code} 历史行情")
    if raw is None or getattr(raw, "empty", True):
        raise RuntimeError(f"{code} 历史行情为空")
    date_col = "date" if "date" in raw.columns else "日期"
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw[date_col]),
            "open": raw["open" if "open" in raw.columns else "开盘"].astype(float),
            "high": raw["high" if "high" in raw.columns else "最高"].astype(float),
            "low": raw["low" if "low" in raw.columns else "最低"].astype(float),
            "close": raw["close" if "close" in raw.columns else "收盘"].astype(float),
            "volume": raw["volume" if "volume" in raw.columns else "成交量"].astype(float),
        }
    ).sort_values("date").reset_index(drop=True)
    _SCREEN_CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False, encoding="utf-8-sig")
    return df


def fetch_benchmark() -> pd.DataFrame:
    """沪深300 指数（新浪），最近交易日缓存。"""
    import akshare as ak

    cache = _SCREEN_CACHE / "benchmark_000300.csv"
    if cache.exists():
        try:
            df = pd.read_csv(cache, parse_dates=["date"])
            if df["date"].max() >= pd.Timestamp(tsdata.last_trade_date()):
                return df
        except Exception:  # noqa: BLE001
            pass
    _apply_proxy()
    index_fn = _ak_call(ak, "index_zh_daily_sina", "stock_zh_index_daily")
    raw = index_fn(symbol="sh000300")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["date"]),
            "close": raw["close"].astype(float),
        }
    ).sort_values("date").reset_index(drop=True)
    _SCREEN_CACHE.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False, encoding="utf-8-sig")
    return df


def market_regime(bench: pd.DataFrame) -> str:
    """固定规则识别市场状态，不做参数拟合。"""
    if bench is None or len(bench) < 60:
        return "neutral"
    close = pd.to_numeric(bench["close"], errors="coerce").dropna()
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ret5 = close.iloc[-1] / close.iloc[-6] - 1
    vol20 = close.pct_change().tail(20).std()
    if close.iloc[-1] < ma20 and close.iloc[-1] < ma60 and ret5 < -0.02:
        return "risk"
    if close.iloc[-1] > ma20 and ma20 > ma60 and ret5 >= -0.02 and vol20 < 0.03:
        return "trend"
    return "neutral"


def scan(top_n: int | None = None, universe_size: int | None = None, progress_cb=None) -> dict:
    """多因子扫描，返回 top 候选（含因子排名与元信息）。

    progress_cb: 可选回调 progress_cb(done, total)，用于网页进度条。
    """
    universe_size = universe_size or config.SCREEN_UNIVERSE_SIZE

    snap = get_snapshot()
    uni = filter_universe(snap)
    uni = uni.sort_values("成交额", ascending=False).head(universe_size)

    bench = fetch_benchmark()
    bench_ret20 = factors.benchmark_ret20(bench)
    regime = market_regime(bench)
    trade_date = pd.to_datetime(bench["date"].max()).strftime("%Y%m%d")

    # 行业：关键词猜测（Tushare 限频、东财被墙，不再取精确行业）
    # 市值：展示字段已移除（不影响因子评分）

    raw: dict[str, dict] = {}
    meta: dict[str, dict] = {}
    total = len(uni)

    # 预建缓存目录并应用代理策略，避免多线程下并发 mkdir / 改环境变量
    _SCREEN_CACHE.mkdir(parents=True, exist_ok=True)
    data_mod._apply_proxy_policy()

    # 最近一个交易日：周末/节假日复用缓存，避免 300 只无谓重拉与失败
    try:
        last_td = pd.Timestamp(tsdata.last_trade_date())
    except Exception:  # noqa: BLE001
        last_td = pd.Timestamp.today().normalize()

    records = uni.to_dict("records")

    def _worker(r: dict) -> tuple[str, dict, dict] | None:
        """单只股票：拉历史 + 算因子 + 拼元信息；失败返回 None。"""
        code = str(r["代码"])
        try:
            df = fetch_hist(code, last_td)
            f = factors.compute_raw_factors(df, bench_ret20)
            if f is None:
                return None
            m = {
                "symbol": code,
                "name": str(r["名称"]),
                "sector": guess_sector(str(r["名称"])),
                "close": float(r["最新价"]),
                "amount_yi": round(float(r["成交额"]) / 1e8, 1),
                "pct": round(float(r["涨跌幅"]), 2),
            }
            return (code, f, m)
        except Exception:  # noqa: BLE001
            return None

    done = 0
    with ThreadPoolExecutor(max_workers=config.SCREEN_WORKERS) as ex:
        futures = [ex.submit(_worker, r) for r in records]
        for fut in as_completed(futures):
            done += 1
            res = fut.result()
            if res is not None:
                code, f, m = res
                raw[code] = f
                meta[code] = m
            if progress_cb and done % 10 == 0:
                progress_cb(done, total)

    ranks = factors.cross_section_rank(raw)
    tech_scores = factors.composite_score(ranks, None)  # 7因子（无新闻）

    # 取技术分 top 40 作为 LLM 新闻打分的候选
    top_codes = sorted(tech_scores, key=tech_scores.get, reverse=True)[:40]
    candidates = []
    for code in top_codes:
        m = dict(meta.get(code, {}))
        m["tech_score"] = round(tech_scores[code], 4)
        m["ranks"] = {k: round(v, 4) for k, v in ranks.get(code, {}).items()}
        f = raw.get(code, {})
        m["ret5_pct"] = round(f.get("short_mom", 0) * 100, 2)
        m["ret20_pct"] = round(f.get("mid_mom", 0) * 100, 2)
        candidates.append(m)

    return {
        "market_stats": {
            "total": len(snap),
            "filtered": len(uni),
            "universe": universe_size,
            "scanned": total,
            "success": len(raw),
            "success_rate": round(len(raw) / total, 4) if total else 0.0,
            "market_regime": regime,
        },
        "market_regime": regime,
        "candidates": candidates,
    }
