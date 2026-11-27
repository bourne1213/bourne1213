# -*- coding: utf-8 -*-
"""
大模型语义分析（可插拔）：委托 providers.llm_providers。

- LLM_PROVIDER=auto|openai|dictionary
- 未配置 LLM_API_KEY 或钉死 dictionary 时，available() 为 False，调用方回退 sentiment 词典法。
- 所有接口失败均返回 None，绝不抛异常中断主流程；状态见 providers.llm_providers.get_last_status()。
"""
from __future__ import annotations

from providers import llm_providers

# 兼容旧引用
finalize_error = None  # updated via property-like sync below


def available() -> bool:
    return llm_providers.available()


def analyze_news(macro_texts: list[str], stock_texts: dict[str, list[str]]) -> dict | None:
    return llm_providers.analyze_news(macro_texts, stock_texts)


def finalize_picks(candidates: list[dict], macro_texts: list[str], top_n: int) -> list[dict] | None:
    global finalize_error
    result = llm_providers.finalize_picks(candidates, macro_texts, top_n)
    finalize_error = llm_providers.finalize_error
    return result


def test_connection() -> tuple[bool, str]:
    return llm_providers.test_connection()


# Re-export for tests / introspection
resolve_provider = llm_providers.resolve_provider
get_last_status = llm_providers.get_last_status
