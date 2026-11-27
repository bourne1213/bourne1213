# -*- coding: utf-8 -*-
"""
行情数据层。

真实模式通过 providers.market 按配置切换：
  MARKET_PROVIDER=auto|eastmoney|sina|fuyao|tonghuashun
  MARKET_PROVIDER_CHAIN=fuyao,tonghuashun,eastmoney,sina

另支持离线 demo 合成行情，用于无网络/未装 akshare 时测试。

输出统一为 DataFrame，列：date, open, high, low, close, volume
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

import config

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _cache_path(symbol: str) -> Path:
    return config.DATA_DIR / f"{symbol}_{config.PERIOD}.csv"


def _apply_proxy_policy() -> None:
    """
    处理系统代理：akshare 底层用 requests，会读取 Windows 系统代理
    （如 Clash/V2Ray 的 127.0.0.1:7897）。代理没开/抽风时会报 ProxyError。

    config.BYPASS_PROXY=True 时绕过系统代理、直连行情源（国内访问行情源推荐）。
    """
    import os

    if config.BYPASS_PROXY:
        for key in ("http_proxy", "https_proxy", "all_proxy",
                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            os.environ[key] = ""
        os.environ["no_proxy"] = "*"
        os.environ["NO_PROXY"] = "*"
        try:
            import requests.utils
            requests.utils.getproxies = lambda: {}
        except ImportError:
            pass


def looks_like_html(payload) -> bool:
    """行情源偶发返回登录页/反爬 HTML；用于在 JSON 解析前拦截。"""
    if payload is None:
        return False
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return False
    if not isinstance(payload, str):
        return False
    s = payload.lstrip()[:80].lower()
    return s.startswith("<!doctype") or s.startswith("<html") or s.startswith("<?xml")


def ensure_json_payload(payload, source: str = "行情接口"):
    """若 payload 是 HTML 或非法 JSON 字符串则抛清晰错误；dict/list 原样返回。"""
    if isinstance(payload, (dict, list)):
        return payload
    if looks_like_html(payload):
        raise RuntimeError(f"{source}返回了 HTML 而非 JSON（可能被反爬/限流）")
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            preview = payload[:60].replace("\n", " ")
            raise RuntimeError(
                f"{source} JSON 解析失败: {exc.msg}（开头: {preview!r}）"
            ) from exc
    return payload


def _retry(fn, times: int = 2, delay: float = 1.0):
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < times - 1:
                time.sleep(delay)
    raise last


def fetch_fuyao(symbol: str) -> pd.DataFrame:
    """兼容入口：委托 providers.market。"""
    from providers import market as market_providers
    return market_providers.fetch_fuyao(symbol)


def fetch_tonghuashun(symbol: str) -> pd.DataFrame:
    from providers import market as market_providers
    return market_providers.fetch_tonghuashun(symbol)


def fetch_eastmoney(symbol: str) -> pd.DataFrame:
    from providers import market as market_providers
    return market_providers.fetch_eastmoney(symbol)


def fetch_sina(symbol: str) -> pd.DataFrame:
    from providers import market as market_providers
    return market_providers.fetch_sina(symbol)


def fetch_akshare(symbol: str) -> pd.DataFrame:
    """拉取真实 A 股行情：按 MARKET_PROVIDER / CHAIN 切换适配器。"""
    from providers import market as market_providers
    return market_providers.fetch(symbol)


def load_or_fetch(symbol: str, refresh: bool = False) -> pd.DataFrame:
    """
    读本地缓存；缓存自「最近一个交易日」以来（含当日）复用，否则重新拉取。
    - 交易日当天只拉一次；收盘后如需当日完整 K 线，用 --refresh 强制重拉。
    - 周末/节假日复用上一交易日的缓存，避免无谓重拉与取数失败。
    refresh=True 强制重拉。
    """
    import datetime

    cache = _cache_path(symbol)
    if cache.exists() and not refresh:
        try:
            import tsdata  # 惰性导入，避免 data<->tsdata 循环引用
            last_td = datetime.date.fromisoformat(tsdata.last_trade_date())
            mtime = datetime.datetime.fromtimestamp(cache.stat().st_mtime).date()
            if mtime >= last_td:
                return pd.read_csv(cache, parse_dates=["date"])
        except Exception:  # noqa: BLE001
            pass

    df = fetch_akshare(symbol)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False, encoding="utf-8-sig")
    return df


def last_data_date(df: pd.DataFrame) -> str:
    """返回 DataFrame 最后一根 K 线的日期（YYYY-MM-DD），空数据返回 ''。"""
    if df is None or df.empty or "date" not in df:
        return ""
    return str(pd.to_datetime(df["date"]).max().date())


def generate_demo(symbol: str = "600519", days: int = 500, seed: int | None = None) -> pd.DataFrame:
    """生成离线合成行情（几何随机游走 + 趋势），用于无网络测试。"""
    import numpy as np

    if seed is None:
        seed = sum(ord(c) for c in symbol) % (2**31)  # 按代码生成不同的确定性序列

    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today(), periods=days)
    # 叠加一段趋势，让均线交叉信号能够出现
    trend = np.linspace(0, 0.4, days)
    returns = rng.normal(0.0005, 0.015, days) + trend / days
    close = 50.0 * np.exp(np.cumsum(returns))
    open_ = close * (1 + rng.normal(0, 0.004, days))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, days)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, days)))
    volume = rng.integers(1_000_000, 5_000_000, days)

    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
