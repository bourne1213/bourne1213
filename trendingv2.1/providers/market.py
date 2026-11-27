# -*- coding: utf-8 -*-
"""
Market-data adapters.

Each adapter returns a DataFrame with columns:
date, open, high, low, close, volume

Config / env:
  MARKET_PROVIDER=auto|eastmoney|sina|fuyao|tonghuashun
  MARKET_PROVIDER_CHAIN=fuyao,tonghuashun,eastmoney,sina
  FUYAO_API_KEY / FUYAO_BASE_URL
  TONGHUASHUN_API_KEY / TONGHUASHUN_BASE_URL
"""
from __future__ import annotations

import json
from typing import Callable

import pandas as pd
import requests

import config
from providers.base import ProviderStatus

Fetcher = Callable[[str], pd.DataFrame]

# Last successful / attempted chain status (for dashboard)
_last_status = ProviderStatus(
    name="market",
    ok=False,
    detail="尚未取数",
    provider="",
)


def get_last_status() -> ProviderStatus:
    return _last_status


def _set_status(**kwargs) -> None:
    global _last_status
    cur = _last_status.to_dict()
    cur.update(kwargs)
    extras = cur.pop("extras", {})
    # rebuild cleanly
    _last_status = ProviderStatus(
        name=cur.get("name", "market"),
        ok=bool(cur.get("ok")),
        detail=str(cur.get("detail", "")),
        provider=str(cur.get("provider", "")),
        fallback=str(cur.get("fallback", "")),
        extras=extras if isinstance(extras, dict) else {},
    )


def _sina_symbol(symbol: str) -> str:
    return ("sh" if symbol[0] in "569" else "sz") + symbol


def _normalize_ohlcv(rows: list[dict], source: str) -> pd.DataFrame:
    """Accept common Chinese/English OHLCV field aliases."""
    if not rows:
        raise RuntimeError(f"{source} 返回空 K 线")
    mapped = []
    for r in rows:
        def pick(*keys, default=None):
            for k in keys:
                if k in r and r[k] not in (None, ""):
                    return r[k]
            return default

        date = pick("date", "Date", "日期", "time", "datetime", "交易日期")
        if date is None:
            continue
        mapped.append({
            "date": pd.to_datetime(date),
            "open": float(pick("open", "Open", "开盘", "开盘价", default=0)),
            "high": float(pick("high", "High", "最高", "最高价", default=0)),
            "low": float(pick("low", "Low", "最低", "最低价", default=0)),
            "close": float(pick("close", "Close", "收盘", "收盘价", default=0)),
            "volume": float(pick("volume", "Volume", "成交量", "vol", default=0)),
        })
    if not mapped:
        raise RuntimeError(f"{source} 无法解析 K 线字段")
    df = pd.DataFrame(mapped)
    if config.START_DATE:
        df = df[df["date"] >= pd.Timestamp(config.START_DATE)]
    return df.sort_values("date").reset_index(drop=True)


def fetch_http_kline(
    symbol: str,
    *,
    base_url: str,
    api_key: str,
    label: str,
) -> pd.DataFrame:
    """Generic OpenAI-style bearer JSON K-line endpoint.

    Expected:
      GET {base_url}/kline?symbol=600519&period=daily&adjust=qfq
      Authorization: Bearer {api_key}
      JSON: {"data":[{date,open,high,low,close,volume}, ...]}
           or a bare list, or {"klines":[...]}
    """
    if not api_key:
        raise RuntimeError(f"未配置 {label} API key，跳过")
    if not base_url:
        raise RuntimeError(f"已配置 {label} key 但未设置 BASE_URL")

    url = base_url.rstrip("/") + "/kline"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (compatible; trendingv2.1/1.0; +local)"
        ),
    }
    params = {
        "symbol": symbol,
        "period": config.PERIOD,
        "adjust": config.ADJUST or "qfq",
        "start": config.START_DATE,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    from data import ensure_json_payload

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        payload = resp.text
    payload = ensure_json_payload(payload, label)

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("data") or payload.get("klines") or payload.get("items") or []
        if isinstance(rows, dict):
            rows = rows.get("list") or rows.get("items") or []
    else:
        raise RuntimeError(f"{label} 响应格式无法识别")
    if not isinstance(rows, list):
        raise RuntimeError(f"{label} data 不是列表")
    return _normalize_ohlcv(rows, label)



def fetch_fuyao(symbol: str) -> pd.DataFrame:
    return fetch_http_kline(
        symbol,
        base_url=config.FUYAO_BASE_URL,
        api_key=config.FUYAO_API_KEY,
        label="Fuyao",
    )


def fetch_tonghuashun(symbol: str) -> pd.DataFrame:
    return fetch_http_kline(
        symbol,
        base_url=config.TONGHUASHUN_BASE_URL,
        api_key=config.TONGHUASHUN_API_KEY,
        label="同花顺",
    )


def _ak_call(ak, *names: str):
    """Return the first available akshare function among renamed aliases."""
    for name in names:
        fn = getattr(ak, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(f"akshare 缺少接口: {' / '.join(names)}")


def fetch_eastmoney(symbol: str) -> pd.DataFrame:
    import akshare as ak
    from data import ensure_json_payload, looks_like_html

    hist = _ak_call(ak, "stock_zh_a_hist_em", "stock_zh_a_hist")
    try:
        raw = hist(
            symbol=symbol,
            period=config.PERIOD,
            start_date=config.START_DATE,
            end_date=pd.Timestamp.today().strftime("%Y%m%d"),
            adjust=config.ADJUST,
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"东方财富 JSON 解析失败（可能返回 HTML）: {exc}") from exc
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
    import akshare as ak
    from data import ensure_json_payload, looks_like_html

    # 新版: stock_zh_a_hist_sina；旧版: stock_zh_a_daily
    hist = _ak_call(ak, "stock_zh_a_hist_sina", "stock_zh_a_daily")
    try:
        if hist.__name__ == "stock_zh_a_hist_sina":
            raw = hist(
                symbol=_sina_symbol(symbol),
                period=config.PERIOD if config.PERIOD in ("daily", "weekly", "monthly") else "daily",
                start_date=config.START_DATE,
                end_date=pd.Timestamp.today().strftime("%Y%m%d"),
                adjust="qfq",
            )
        else:
            raw = hist(symbol=_sina_symbol(symbol), adjust="qfq")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"新浪 JSON 解析失败（可能返回 HTML）: {exc}") from exc
    if isinstance(raw, str) or looks_like_html(raw):
        ensure_json_payload(raw, "新浪")
    if raw is None or (hasattr(raw, "empty") and raw.empty):
        raise RuntimeError(f"新浪未返回 {symbol} 的行情")
    # hist_sina / daily 都用英文列
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
    )
    df = df[df["date"] >= pd.Timestamp(config.START_DATE)]
    return df.sort_values("date").reset_index(drop=True)


# label registry only — callables resolved at runtime so tests can patch them
REGISTRY_LABELS: dict[str, str] = {
    "fuyao": "Fuyao",
    "tonghuashun": "同花顺",
    "eastmoney": "东方财富",
    "sina": "新浪",
}


def _fetcher(key: str) -> Fetcher:
    return {
        "fuyao": fetch_fuyao,
        "tonghuashun": fetch_tonghuashun,
        "eastmoney": fetch_eastmoney,
        "sina": fetch_sina,
    }[key]


# Back-compat alias used by older imports/tests
REGISTRY: dict[str, tuple[Fetcher, str]] = {
    k: (_fetcher(k), lab) for k, lab in REGISTRY_LABELS.items()
}



def _key_ready(name: str) -> bool:
    if name == "fuyao":
        return bool(config.FUYAO_API_KEY and config.FUYAO_BASE_URL)
    if name == "tonghuashun":
        return bool(config.TONGHUASHUN_API_KEY and config.TONGHUASHUN_BASE_URL)
    return name in REGISTRY_LABELS


def resolve_chain() -> list[tuple[Fetcher, str, str]]:
    """Return ordered (fn, display_name, key) adapters to try."""
    pin = (config.MARKET_PROVIDER or "auto").strip().lower()
    if pin and pin != "auto":
        if pin not in REGISTRY_LABELS:
            raise RuntimeError(
                f"未知 MARKET_PROVIDER={pin!r}；可选: auto,{','.join(REGISTRY_LABELS)}"
            )
        if not _key_ready(pin) and pin in ("fuyao", "tonghuashun"):
            raise RuntimeError(
                f"MARKET_PROVIDER={pin} 但未配置对应 API key / BASE_URL"
            )
        return [(_fetcher(pin), REGISTRY_LABELS[pin], pin)]

    chain_raw = config.MARKET_PROVIDER_CHAIN or "fuyao,tonghuashun,eastmoney,sina"
    ordered: list[tuple[Fetcher, str, str]] = []
    seen: set[str] = set()
    for part in chain_raw.split(","):
        key = part.strip().lower()
        if not key or key in seen or key not in REGISTRY_LABELS:
            continue
        if not _key_ready(key):
            continue
        seen.add(key)
        ordered.append((_fetcher(key), REGISTRY_LABELS[key], key))
    # Always ensure eastmoney/sina as last-resort if somehow empty
    if not ordered:
        for key in ("eastmoney", "sina"):
            ordered.append((_fetcher(key), REGISTRY_LABELS[key], key))
    return ordered



def fetch(symbol: str, retry_times: int = 2, delay: float = 1.0) -> pd.DataFrame:
    """Try configured market providers in order; record which one won."""
    import time

    from data import _apply_proxy_policy

    _apply_proxy_policy()
    try:
        import akshare  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        # HTTP-only providers (fuyao/ths) still work without akshare
        chain = [(fn, lab, k) for fn, lab, k in resolve_chain() if k in ("fuyao", "tonghuashun")]
        if not chain:
            raise RuntimeError(
                "未安装 akshare，且未配置 Fuyao/同花顺 HTTP 行情源。\n"
                "请先执行: pip install akshare\n"
                "或者使用离线模式: python runner.py --demo"
            ) from exc
    else:
        chain = resolve_chain()

    errors: list[str] = []
    for fn, label, key in chain:
        last = None
        for i in range(retry_times):
            try:
                df = fn(symbol)
                _set_status(
                    ok=True,
                    provider=key,
                    detail=f"使用 {label}",
                    fallback="",
                    extras={"tried": [e.split(":")[0] for e in errors]},
                )
                return df
            except Exception as exc:  # noqa: BLE001
                last = exc
                if i < retry_times - 1:
                    time.sleep(delay)
        errors.append(f"{label}: {last}")

    _set_status(
        ok=False,
        provider="",
        detail="所有行情源均失败",
        fallback="",
        extras={"errors": errors},
    )
    raise RuntimeError("所有数据源均失败：" + " | ".join(errors))
