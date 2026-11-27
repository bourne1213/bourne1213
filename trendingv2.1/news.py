# -*- coding: utf-8 -*-
"""
新闻数据层（可插拔）：委托 providers.news_providers。

NEWS_SOURCES=cctv,cls,stock_em（逗号分隔）；任一失败自动跳过，不影响看板。
"""
from __future__ import annotations

from providers import news_providers


def fetch_cctv(date: str | None = None) -> list[dict]:
    return news_providers.fetch_cctv(date)


def fetch_cls() -> list[dict]:
    return news_providers.fetch_cls()


def fetch_stock_news(symbol: str) -> list[dict]:
    items, _ = news_providers.fetch_stock_news(symbol)
    return items


def fetch_macro() -> list[dict]:
    items, _, _ = news_providers.fetch_macro()
    return items


def fetch_all(symbols: dict[str, str]) -> dict:
    return news_providers.fetch_all(symbols)


get_last_status = news_providers.get_last_status
parse_enabled_sources = news_providers.parse_enabled_sources
