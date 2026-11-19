# -*- coding: utf-8 -*-
"""
自选股管理：本地 JSON 存储，支持网页增删，也可直接在文件里改。
每项：{symbol: "600519", name: "贵州茅台", sector: "消费"}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import config

WATCHLIST_FILE = Path(__file__).parent / "watchlist.json"


def load() -> list[dict]:
    """读取自选股列表；文件不存在或为空时回退到 config.STOCKS。"""
    if WATCHLIST_FILE.exists():
        try:
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:  # noqa: BLE001
            pass
    return [{"symbol": k, "name": v} for k, v in config.STOCKS.items()]


def save(items: list[dict]) -> None:
    """原子写入。"""
    tmp = WATCHLIST_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, WATCHLIST_FILE)


def as_dict(items: list[dict] | None = None) -> dict[str, str]:
    """转成 {symbol: name} 供报告/交易使用。"""
    items = items if items is not None else load()
    return {str(it["symbol"]): str(it.get("name", it["symbol"])) for it in items}


def sector_map(items: list[dict] | None = None) -> dict[str, str]:
    """symbol -> sector（优先自选股里填的板块，其次 config.STOCK_SECTOR）。

    自选股填「其他」/空字符串时视为未配置，回退到 STOCK_SECTOR。
    """
    items = items if items is not None else load()
    m = dict(config.STOCK_SECTOR)
    for it in items:
        sec = str(it.get("sector") or "").strip()
        if not sec or sec in ("其他", "其它", "综合"):
            continue
        m[str(it["symbol"])] = sec
    return m
