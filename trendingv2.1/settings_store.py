# -*- coding: utf-8 -*-
"""
Runtime provider settings for the web board.

Priority: providers.json (UI) > environment / config.py defaults.

Saving from the dashboard writes providers.json and immediately mutates
config.* so the next /api/report refresh uses the new providers without
restarting Flask. Secrets may be stored locally (file is gitignored);
env vars remain the recommended place for keys in shared machines.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import config

SETTINGS_FILE = Path(__file__).parent / "providers.json"
_lock = threading.RLock()  # re-entrant: merge_and_save → apply

# Keys the UI / JSON may override. Values are snapshotted from config at import
# (already seeded from env) so we can fall back when a JSON key is cleared.
OVERRIDABLE = (
    "MARKET_PROVIDER",
    "MARKET_PROVIDER_CHAIN",
    "FUYAO_API_KEY",
    "FUYAO_BASE_URL",
    "TONGHUASHUN_API_KEY",
    "TONGHUASHUN_BASE_URL",
    "NEWS_SOURCES",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_PROXY",
)

SECRET_KEYS = {
    "FUYAO_API_KEY",
    "TONGHUASHUN_API_KEY",
    "LLM_API_KEY",
}

_BASELINE: dict[str, Any] = {k: getattr(config, k) for k in OVERRIDABLE}


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return value[:3] + "••••" + value[-2:]


def load_raw() -> dict[str, Any]:
    """Load overrides from disk; missing/invalid file → {}."""
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_raw(overrides: dict[str, Any]) -> dict[str, Any]:
    """Write cleaned overrides; empty dict deletes the file."""
    cleaned: dict[str, Any] = {}
    for key in OVERRIDABLE:
        if key not in overrides:
            continue
        val = overrides[key]
        if val is None:
            continue
        if isinstance(val, str):
            val = val.strip()
        # Skip empty non-secret clears — caller should omit to keep baseline
        cleaned[key] = val

    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not cleaned:
        if SETTINGS_FILE.exists():
            SETTINGS_FILE.unlink()
        return {}
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)
    return cleaned


def apply(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply JSON overrides onto config module. Returns effective values."""
    with _lock:
        raw = load_raw() if overrides is None else dict(overrides)
        effective: dict[str, Any] = {}
        for key in OVERRIDABLE:
            if key in raw and raw[key] is not None and str(raw[key]).strip() != "":
                value = raw[key]
                if isinstance(value, str):
                    value = value.strip()
                setattr(config, key, value)
            else:
                setattr(config, key, _BASELINE[key])
            effective[key] = getattr(config, key)
        return effective


def merge_and_save(patch: dict[str, Any]) -> dict[str, Any]:
    """
    Merge a UI patch into providers.json and apply.

    Secret fields: omit or send "" to leave unchanged; send a new value to
    replace; send the literal "__clear__" to remove the override (fall back
    to env/baseline).
    """
    with _lock:
        current = load_raw()
        for key in OVERRIDABLE:
            if key not in patch:
                continue
            val = patch[key]
            if key in SECRET_KEYS:
                if val is None or val == "":
                    continue  # keep existing override / baseline
                if val == "__clear__":
                    current.pop(key, None)
                    continue
                current[key] = str(val).strip()
                continue
            if val is None or (isinstance(val, str) and val.strip() == ""):
                current.pop(key, None)
                continue
            current[key] = val.strip() if isinstance(val, str) else val
        saved = save_raw(current)
        return apply(saved)


def catalog() -> dict[str, Any]:
    """Options shown in the Settings panel."""
    return {
        "market_providers": [
            {"id": "auto", "label": "自动（按链路回退）"},
            {"id": "eastmoney", "label": "东方财富"},
            {"id": "sina", "label": "新浪"},
            {"id": "fuyao", "label": "Fuyao（需 API key）"},
            {"id": "tonghuashun", "label": "同花顺（需 API key）"},
        ],
        "llm_providers": [
            {"id": "auto", "label": "自动（有 key 用大模型，否则词典）"},
            {"id": "openai", "label": "OpenAI 兼容（DeepSeek / 自建）"},
            {"id": "dictionary", "label": "词典法（离线）"},
        ],
        "news_presets": [
            {"id": "cctv,cls,stock_em", "label": "全部（CCTV + 财联社 + 东财个股）"},
            {"id": "cls,stock_em", "label": "财联社 + 东财个股"},
            {"id": "cls", "label": "仅财联社"},
            {"id": "cctv", "label": "仅 CCTV"},
            {"id": "stock_em", "label": "仅东财个股"},
            {"id": "none", "label": "关闭新闻"},
        ],
    }


def public_view() -> dict[str, Any]:
    """Safe snapshot for GET /api/providers (secrets masked)."""
    apply()  # pick up any external file edits
    overrides = load_raw()
    effective = {k: getattr(config, k) for k in OVERRIDABLE}
    masked = {}
    for k, v in effective.items():
        if k in SECRET_KEYS:
            masked[k] = _mask(str(v or ""))
            masked[k + "_set"] = bool(v)
        else:
            masked[k] = v
    # Active resolution hints
    from providers import llm_providers, market, news_providers

    try:
        market_chain = [
            {"id": key, "label": label}
            for _, label, key in market.resolve_chain()
        ]
    except Exception as exc:  # noqa: BLE001
        market_chain = [{"error": str(exc)[:120]}]

    try:
        llm_resolved = llm_providers.resolve_provider()
    except Exception as exc:  # noqa: BLE001
        llm_resolved = f"error:{exc}"

    news_enabled = news_providers.parse_enabled_sources()

    return {
        "ok": True,
        "file": str(SETTINGS_FILE.name),
        "has_local_file": SETTINGS_FILE.exists(),
        "overrides": {k: ("••••" if k in SECRET_KEYS and k in overrides else overrides.get(k))
                      for k in overrides},
        "effective": masked,
        "active": {
            "market": effective.get("MARKET_PROVIDER", "auto"),
            "market_chain": market_chain,
            "llm": effective.get("LLM_PROVIDER", "auto"),
            "llm_resolved": llm_resolved,
            "llm_model": effective.get("LLM_MODEL"),
            "news": effective.get("NEWS_SOURCES"),
            "news_enabled": news_enabled,
        },
        "status": {
            "market": market.get_last_status().to_dict(),
            "llm": llm_providers.get_last_status().to_dict(),
            "news": news_providers.get_last_status().to_dict(),
        },
        "catalog": catalog(),
        "hint": "保存后点「刷新今日分析」即生效，无需重启。密钥优先用环境变量；面板可覆盖。",
    }


# Apply any existing local file on import (Flask / CLI)
apply()
