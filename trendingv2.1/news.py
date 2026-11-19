# -*- coding: utf-8 -*-
"""
新闻数据层：抓取时政 + 财经快讯 + 个股新闻。

数据源（任一失败自动跳过，不影响其它）：
1. CCTV 新闻联播（时政核心）：ak.news_cctv
2. 财联社电报（财经/政策快讯）：ak.stock_info_global_cls
3. 个股新闻（东财）：ak.stock_news_em

统一返回 list[dict]，每项含 title / content / time / source。
"""
from __future__ import annotations

import pandas as pd

import config
import data as data_mod


def _apply_proxy() -> None:
    data_mod._apply_proxy_policy()


def fetch_cctv(date: str | None = None) -> list[dict]:
    """CCTV 新闻联播文字稿（时政）。"""
    import akshare as ak

    _apply_proxy()
    if date is None:
        date = pd.Timestamp.today().strftime("%Y%m%d")
    raw = ak.news_cctv(date=date)
    out = []
    for _, r in raw.iterrows():
        out.append({
            "title": str(r.get("title", "")),
            "content": str(r.get("content", "")),
            "time": str(r.get("date", date)),
            "source": "CCTV",
        })
    return out


def fetch_cls() -> list[dict]:
    """财联社电报（财经/政策快讯）。"""
    import akshare as ak

    _apply_proxy()
    raw = ak.stock_info_global_cls(symbol="全部")
    out = []
    for _, r in raw.iterrows():
        title = str(r.get("标题", ""))
        content = str(r.get("内容", ""))
        text = f"{title} {content}".strip()
        out.append({
            "title": title,
            "content": text,
            "time": f"{r.get('发布日期', '')} {r.get('发布时间', '')}".strip(),
            "source": "财联社",
        })
    return out


def fetch_stock_news(symbol: str) -> list[dict]:
    """个股新闻（东财）。"""
    import akshare as ak

    _apply_proxy()
    raw = ak.stock_news_em(symbol=symbol)
    out = []
    for _, r in raw.iterrows():
        title = str(r.get("新闻标题", ""))
        content = str(r.get("新闻内容", ""))
        text = f"{title} {content}".strip()
        out.append({
            "title": title,
            "content": text,
            "time": str(r.get("发布时间", "")),
            "source": "东财个股",
        })
    return out


def _safe(fn, label: str) -> list[dict]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        msg = f"[news] {label} 抓取失败: {type(exc).__name__} {str(exc)[:60]}"
        print(msg)
        try:
            import notify  # 惰性导入：把失败原因也写进 signals.log，便于事后排查
            notify.logger.warning("%s", msg)
        except Exception:  # noqa: BLE001
            pass
        return []


def fetch_macro() -> list[dict]:
    """时政 + 财经快讯（宏观面，供板块情绪用）。"""
    items = _safe(fetch_cctv, "CCTV")
    items += _safe(fetch_cls, "财联社")
    return items


def fetch_all(symbols: dict[str, str]) -> dict:
    """
    抓取宏观面（时政+快讯）和每只股票的个股新闻。
    返回 {"macro": list[dict], "stocks": {symbol: list[dict]}}
    """
    result = {"macro": fetch_macro(), "stocks": {}}
    for symbol in symbols:
        result["stocks"][symbol] = _safe(lambda s=symbol: fetch_stock_news(s), symbol)
    return result
