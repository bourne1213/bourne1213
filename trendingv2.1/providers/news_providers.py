# -*- coding: utf-8 -*-
"""
News source adapters (fail-soft).

Selectable via NEWS_SOURCES=cctv,cls,stock_em (comma-separated).
Unknown sources are skipped with a warning; empty result does not break the board.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

import config
from providers.base import ProviderStatus

_last_status = ProviderStatus(
    name="news",
    ok=False,
    detail="尚未抓取",
    provider="",
)


def get_last_status() -> ProviderStatus:
    return _last_status


def _set_status(**kwargs) -> None:
    global _last_status
    cur = _last_status.to_dict()
    cur.update(kwargs)
    extras = cur.pop("extras", {})
    _last_status = ProviderStatus(
        name=cur.get("name", "news"),
        ok=bool(cur.get("ok")),
        detail=str(cur.get("detail", "")),
        provider=str(cur.get("provider", "")),
        fallback=str(cur.get("fallback", "")),
        extras=extras if isinstance(extras, dict) else {},
    )


def _apply_proxy() -> None:
    from data import _apply_proxy_policy
    _apply_proxy_policy()


def _ak_call(ak, *names: str):
    for name in names:
        fn = getattr(ak, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(f"akshare 缺少接口: {' / '.join(names)}")


def fetch_cctv(date: str | None = None) -> list[dict]:
    import akshare as ak

    _apply_proxy()
    if date is None:
        date = pd.Timestamp.today().strftime("%Y%m%d")
    cctv = _ak_call(ak, "alternative_news_cctv", "news_cctv")
    raw = cctv(date=date)
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
    import akshare as ak

    _apply_proxy()
    cls_fn = _ak_call(ak, "stock_news_global_cls", "stock_info_global_cls")
    raw = cls_fn(symbol="全部")
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


def fetch_stock_em(symbol: str) -> list[dict]:
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


MACRO_LABELS: dict[str, str] = {
    "cctv": "CCTV",
    "cls": "财联社",
}

STOCK_LABELS: dict[str, str] = {
    "stock_em": "东财个股",
    "eastmoney": "东财个股",  # alias
}


def _macro_fn(key: str) -> Callable[[], list[dict]]:
    return {"cctv": fetch_cctv, "cls": fetch_cls}[key]


def _stock_fn(key: str) -> Callable[[str], list[dict]]:
    return fetch_stock_em



def parse_enabled_sources() -> list[str]:
    raw = (config.NEWS_SOURCES or "cctv,cls,stock_em").strip()
    if not raw or raw.lower() in ("none", "off", "disabled"):
        return []
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _safe(fn, label: str) -> tuple[list[dict], str | None]:
    try:
        return fn(), None
    except Exception as exc:  # noqa: BLE001
        msg = f"{label} 抓取失败: {type(exc).__name__} {str(exc)[:60]}"
        print(f"[news] {msg}")
        try:
            import notify
            notify.logger.warning("%s", f"[news] {msg}")
        except Exception:  # noqa: BLE001
            pass
        return [], msg


def fetch_macro() -> tuple[list[dict], list[str], list[str]]:
    """Return (items, ok_labels, fail_messages)."""
    enabled = parse_enabled_sources()
    items: list[dict] = []
    ok_labels: list[str] = []
    fails: list[str] = []
    for key in enabled:
        if key not in MACRO_LABELS:
            continue
        label = MACRO_LABELS[key]
        chunk, err = _safe(_macro_fn(key), label)
        if err:
            fails.append(err)
        elif chunk:
            items.extend(chunk)
            ok_labels.append(label)
        else:
            fails.append(f"{label} 返回空")
    return items, ok_labels, fails


def fetch_stock_news(symbol: str, enabled: list[str] | None = None) -> tuple[list[dict], list[str]]:
    enabled = enabled if enabled is not None else parse_enabled_sources()
    items: list[dict] = []
    fails: list[str] = []
    used = False
    for key in enabled:
        if key not in STOCK_LABELS:
            continue
        used = True
        label = STOCK_LABELS[key]
        fn = _stock_fn(key)
        chunk, err = _safe(lambda s=symbol, f=fn: f(s), f"{label}:{symbol}")
        if err:
            fails.append(err)
        else:
            items.extend(chunk)
    if not used:
        # stock sources disabled intentionally
        pass
    return items, fails


def fetch_all(symbols: dict[str, str]) -> dict:
    """
    Grab macro + per-symbol news according to NEWS_SOURCES.
    Always returns a complete structure; failures are soft.
    """
    enabled = parse_enabled_sources()
    unknown = [
        s for s in enabled
        if s not in MACRO_LABELS and s not in STOCK_LABELS
    ]
    macro, ok_labels, fails = fetch_macro()
    stocks: dict[str, list[dict]] = {}
    for symbol in symbols:
        items, stock_fails = fetch_stock_news(symbol, enabled)
        stocks[symbol] = items
        fails.extend(stock_fails)

    any_ok = bool(macro) or any(stocks.values())
    detail_parts = []
    if ok_labels:
        detail_parts.append("宏观: " + ",".join(ok_labels))
    stock_ok = sum(1 for v in stocks.values() if v)
    if stock_ok:
        detail_parts.append(f"个股新闻 {stock_ok}/{len(symbols)}")
    if unknown:
        detail_parts.append("未知源已忽略: " + ",".join(unknown))
    if fails and not any_ok:
        detail_parts.append("全部失败（看板继续，情绪用空）")
    elif fails:
        detail_parts.append(f"{len(fails)} 个源失败（已跳过）")

    _set_status(
        ok=any_ok,
        provider=",".join(enabled) if enabled else "none",
        detail="；".join(detail_parts) if detail_parts else "未启用任何新闻源",
        fallback="" if any_ok else "empty",
        extras={
            "enabled": enabled,
            "ok_sources": ok_labels,
            "failures": fails[:8],
            "unknown": unknown,
        },
    )
    return {"macro": macro, "stocks": stocks}
