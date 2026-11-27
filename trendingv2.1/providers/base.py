# -*- coding: utf-8 -*-
"""Shared provider status helpers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ProviderStatus:
    """Visible status for one subsystem (market / llm / news)."""

    name: str
    ok: bool
    detail: str = ""
    provider: str = ""
    fallback: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        extras = d.pop("extras", {}) or {}
        d.update(extras)
        return d


def provider_snapshot(
    market: ProviderStatus | None = None,
    llm: ProviderStatus | None = None,
    news: ProviderStatus | None = None,
) -> dict[str, Any]:
    """Compact providers block for report.json / dashboard chips."""
    out: dict[str, Any] = {}
    if market is not None:
        out["market"] = market.to_dict()
    if llm is not None:
        out["llm"] = llm.to_dict()
    if news is not None:
        out["news"] = news.to_dict()
    return out
