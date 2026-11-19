# -*- coding: utf-8 -*-
"""
大模型语义分析（可选）：调用 OpenAI 兼容接口（默认 DeepSeek）对新闻做分析。

- 未配置 LLM_API_KEY 时，available() 返回 False，调用方应回退到 sentiment.py 词典法。
- 所有接口失败均返回 None，绝不抛异常中断主流程。
"""
from __future__ import annotations

import json
import re

import requests

import config


def available() -> bool:
    return bool(config.LLM_API_KEY and config.LLM_BASE_URL and config.LLM_MODEL)


def _chat(messages: list[dict], temperature: float = 0.2, max_tokens: int = 3000) -> str:
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    proxies = None
    if config.LLM_PROXY:
        proxies = {"http": config.LLM_PROXY, "https": config.LLM_PROXY}
    resp = requests.post(url, headers=headers, json=payload, timeout=90, proxies=proxies)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    return json.loads(text)


def analyze_news(macro_texts: list[str], stock_texts: dict[str, list[str]]) -> dict | None:
    """分析新闻，返回 {summary, sentiment, themes, stocks}；失败返回 None。"""
    if not available():
        return None

    macro_snip = "\n".join(t[:120] for t in macro_texts[:40])
    stock_snip = "\n".join(
        f"{sym}: " + " / ".join(t[:100] for t in txts[:5])
        for sym, txts in stock_texts.items()
    )
    prompt = (
        "你是A股投研助手。请分析下面的新闻，只输出一个JSON对象（不要markdown、不要解释）。\n"
        "JSON 结构：\n"
        '{"summary": "一句话市场点评", "sentiment": -1到1的市场情绪, '
        '"themes": [{"theme": "板块/主题", "sentiment": -1到1, "reason": "一句依据"}], '
        '"stocks": {"股票代码": {"sentiment": -1到1, "note": "一句点评"}}}\n\n'
        f"=== 时政/宏观新闻 ===\n{macro_snip or '（无）'}\n\n"
        f"=== 个股新闻 ===\n{stock_snip or '（无）'}\n"
    )
    try:
        content = _chat([
            {"role": "system", "content": "你只输出合法JSON。"},
            {"role": "user", "content": prompt},
        ])
        return _parse_json(content)
    except Exception as exc:  # noqa: BLE001
        print(f"[llm] 新闻分析失败，回退词典法: {type(exc).__name__} {str(exc)[:80]}")
        return None


# 最近一次 finalize_picks 的失败原因（供 report.py 在报告里显式标注，避免静默回退）
finalize_error: str | None = None


def _norm_sym(s: str) -> str:
    """把带前缀/带后缀的代码归一到纯 6 位数字，便于宽松匹配。

    'sz300759' / '300759.SZ' / '300759' -> '300759'
    """
    s = str(s).strip().lower()
    if "." in s:
        s = s.split(".")[0]
    if len(s) == 8 and s[:2] in ("sh", "sz", "bj") and s[2:].isdigit():
        return s[2:]
    return s


def finalize_picks(candidates: list[dict], macro_texts: list[str], top_n: int) -> list[dict] | None:
    """
    让大模型对候选池股票打分。模型只允许返回 {symbol, news_score, reason}，
    不得返回名称/价格/行业/技术指标（证券主数据一律以数据源为准）。

    校验：symbol 必须存在于候选池（支持不带 sh/sz/bj 前缀的宽松匹配）、
    不允许重复、news_score 必须在 [-1,1]、最多返回 top_n 个；任一无效即丢弃。
    失败自动重试一次；仍失败返回 None（回退技术排序），并记录 finalize_error。
    """
    global finalize_error
    finalize_error = None
    if not available() or not candidates:
        return None

    valid_symbols = {c["symbol"] for c in candidates}
    # 纯代码 -> 候选里的带前缀代码（模型常回 "300759" 而非 "sz300759"）
    norm_map: dict[str, str] = {}
    for sym in valid_symbols:
        norm_map.setdefault(_norm_sym(sym), sym)

    # 编号新闻，让模型引用证据来源
    numbered = "\n".join(f"N{i}: {t[:120]}" for i, t in enumerate(macro_texts[:40], 1))
    cand_snip = "\n".join(f"- {c['symbol']} {c['name']}" for c in candidates[:30])
    prompt = (
        "你是A股投研助手。下面给出【今日新闻】和一个【股票候选池】。\n"
        "任务：只对【股票候选池】里的股票打新闻情绪分。\n"
        "只输出一个JSON：{\"picks\":[{\"symbol\":\"候选池里的代码\",\"news_score\":-1到1,\"reason\":\"一句话，引用新闻编号如N3\"}]}\n"
        "铁律（违反即视为失败）：\n"
        "1. symbol 只能逐字取自【股票候选池】里的代码；候选池之外的公司一律不得输出，\n"
        "   即使新闻里提到了它们（例如中微半导、盛科通信等），也不要为它们编造代码。\n"
        "2. 只给「新闻中确有相关信息」的候选股打非零分；新闻没提到的候选股 news_score 填 0。\n"
        "3. news_score 范围 [-1,1]，正=利好，负=利空。\n"
        "4. reason 只能依据下面新闻，引用其编号，不得编造输入里没有的事实。\n"
        f"最多返回 {top_n} 条（优先返回新闻相关性最强的候选股）。\n\n"
        f"=== 今日新闻 ===\n{numbered or '（无）'}\n\n"
        f"=== 股票候选池（只允许这些代码）===\n{cand_snip}\n"
    )

    last_exc: Exception | None = None
    for attempt in range(2):  # 首次 + 一次重试（第二次用更强约束纠正）
        sys_msg = ("你只输出合法JSON。你只能给候选池里的股票打分，"
                   "绝不为候选池之外的公司输出代码。")
        if attempt == 1:
            sys_msg += " 上一轮你返回了候选池之外的代码，这次严格只从候选池里选。"
        try:
            content = _chat([
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": prompt},
            ])
            data = _parse_json(content)
            out: list[dict] = []
            seen: set[str] = set()
            for item in data.get("picks", []):
                sym = str(item.get("symbol", "")).strip()
                if sym not in valid_symbols:
                    sym = norm_map.get(_norm_sym(sym), sym)  # 宽松匹配一次
                try:
                    score = float(item.get("news_score"))
                except (TypeError, ValueError):
                    continue
                # 校验：代码必须存在、不重复、分数合法
                if sym not in valid_symbols or sym in seen:
                    continue
                if not (-1.0 <= score <= 1.0):
                    continue
                seen.add(sym)
                out.append({
                    "symbol": sym,
                    "news_score": round(score, 3),
                    "reason": str(item.get("reason", "")).strip(),
                })
                if len(out) >= top_n:
                    break
            if out:
                return out
            last_exc = RuntimeError("模型返回的 picks 全部未通过校验（代码/分数不合法）")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc

    finalize_error = f"{type(last_exc).__name__} {str(last_exc)[:120]}" if last_exc else "未知错误"
    print(f"[llm] 新闻打分失败，回退技术排序: {finalize_error}")
    return None


def test_connection() -> tuple[bool, str]:
    """连通性测试：验证 key / base_url / 模型是否配置正确。返回 (是否成功, 说明)。"""
    if not available():
        return False, "未配置 LLM_API_KEY（请在 config.py 里填 key）"
    try:
        content = _chat(
            [{"role": "user", "content": "只回复四个字：连接成功"}],
            temperature=0.0,
            max_tokens=16,
        )
        return True, f"连接成功，模型「{config.LLM_MODEL}」返回: {content.strip()}"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {type(exc).__name__} - {str(exc)[:200]}"
