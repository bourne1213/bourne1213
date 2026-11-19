# -*- coding: utf-8 -*-
"""
用户真实持仓管理：本地 JSON 存储，供网页输入/查询。
每项：{symbol: "600519", name: "贵州茅台", shares: 100, cost_price: 1500.0}
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HOLDINGS_FILE = Path(__file__).parent / "holdings.json"


def load() -> list[dict]:
    if HOLDINGS_FILE.exists():
        try:
            data = json.loads(HOLDINGS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def save(items: list[dict]) -> None:
    """原子写入：先写临时文件再替换，避免中断写留下半截 JSON。"""
    tmp = HOLDINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, HOLDINGS_FILE)
