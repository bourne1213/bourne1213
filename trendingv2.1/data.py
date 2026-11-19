# -*- coding: utf-8 -*-
"""
行情数据层。

真实模式数据源（按顺序自动切换，任一成功即返回）：
1. 可选 Fuyao 适配（仅当 FUYAO_API_KEY 已配置；否则跳过）
2. 东方财富（stock_zh_a_hist）
3. 新浪财经（stock_zh_a_daily）

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


def _sina_symbol(symbol: str) -> str:
    """6/5/9 开头 -> 沪市 sh；其余按深市 sz 处理（北交所暂未覆盖）。"""
    return ("sh" if symbol[0] in "569" else "sz") + symbol


def fetch_fuyao(symbol: str) -> pd.DataFrame:
    """可选 Fuyao 适配器桩：无 key 时直接跳过；有 key 但未实现完整协议时给出明确错误。

    不要求配置 API key；缺省走东财/新浪即可。
    """
    if not config.FUYAO_API_KEY:
        raise RuntimeError("未配置 FUYAO_API_KEY，跳过 Fuyao 源")
    if not config.FUYAO_BASE_URL:
        raise RuntimeError("已配置 FUYAO_API_KEY 但未设置 FUYAO_BASE_URL")
    # 桩：预留 HTTP JSON K 线接入点；当前仓库不捆绑第三方协议细节。
    raise RuntimeError(
        f"Fuyao 适配器未实现具体协议（symbol={symbol}）；"
        "请清空 FUYAO_API_KEY 以回退东财/新浪，或自行补全 fetch_fuyao"
    )


def fetch_eastmoney(symbol: str) -> pd.DataFrame:
    """东方财富，前复权历史行情。"""
    import akshare as ak

    try:
        raw = ak.stock_zh_a_hist(
            symbol=symbol,
            period=config.PERIOD,
            start_date=config.START_DATE,
            end_date=pd.Timestamp.today().strftime("%Y%m%d"),
            adjust=config.ADJUST,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"东方财富 JSON 解析失败（可能返回 HTML）: {exc}"
        ) from exc
    if isinstance(raw, str) or looks_like_html(raw):
        ensure_json_payload(raw, "东方财富")
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        raise RuntimeError(f"东方财富未返回 {symbol} 的行情")
    return pd.DataFrame(
        {
            "date": pd.to_datetime(raw["日期"]),
            "open": raw["开盘"].astype(float),
            "high": raw["最高"].astype(float),
            "low": raw["最低"].astype(float),
            "close": raw["收盘"].astype(float),
            "volume": raw["成交量"].astype(float),
        }
    ).sort_values("date").reset_index(drop=True)


def fetch_sina(symbol: str) -> pd.DataFrame:
    """新浪财经，前复权历史行情（备用源）。"""
    import akshare as ak

    try:
        raw = ak.stock_zh_a_daily(symbol=_sina_symbol(symbol), adjust="qfq")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"新浪 JSON 解析失败（可能返回 HTML）: {exc}") from exc
    if isinstance(raw, str) or looks_like_html(raw):
        ensure_json_payload(raw, "新浪")
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        raise RuntimeError(f"新浪未返回 {symbol} 的行情")
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["date"]),
            "open": raw["open"].astype(float),
            "high": raw["high"].astype(float),
            "low": raw["low"].astype(float),
            "close": raw["close"].astype(float),
            "volume": raw["volume"].astype(float),
        }
    )
    df = df[df["date"] >= pd.Timestamp(config.START_DATE)]
    return df.sort_values("date").reset_index(drop=True)


def fetch_akshare(symbol: str) -> pd.DataFrame:
    """拉取真实 A 股行情：可选 Fuyao → 东财 → 新浪，各重试一次。"""
    _apply_proxy_policy()
    try:
        import akshare  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "未安装 akshare，无法拉取真实行情。\n"
            "请先执行: pip install akshare\n"
            "或者使用离线模式: python runner.py --demo"
        ) from exc

    sources = []
    if config.FUYAO_API_KEY:
        sources.append((fetch_fuyao, "Fuyao"))
    sources.extend(((fetch_eastmoney, "东方财富"), (fetch_sina, "新浪")))

    errors = []
    for fn, name in sources:
        try:
            return _retry(lambda f=fn: f(symbol), times=2, delay=1.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    raise RuntimeError("所有数据源均失败：" + " | ".join(errors))


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
