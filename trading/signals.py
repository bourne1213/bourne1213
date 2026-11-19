# -*- coding: utf-8 -*-
"""
信号融合：技术面（双均线） + 消息面（个股新闻/时政板块情绪） -> 买卖建议。

输出统一为 dict：
    {action: 买入/卖出/必须卖出/关注/观望/持有, confidence: 0-100, reasons: [..], detail: {..}}

优先级：止损/止盈/移动止损 → 必须卖出（覆盖综合分）；
持仓时 combined <= HOLDING_SELL_THRESHOLD → 卖出；
否则按 BUY_THRESHOLD / SELL_THRESHOLD。
"""
from __future__ import annotations

import config
import risk
import sentiment
import strategy

# 模拟盘与推送把这两种动作都视为卖出
SELL_ACTIONS = frozenset({"卖出", "必须卖出"})


def recommend(
    symbol: str,
    name: str,
    df,
    stock_news_texts: list[str],
    macro_texts: list[str],
    stock_sent: float | None = None,
    sector_sent: float | None = None,
    sector: str | None = None,
    avg_cost: float | None = None,
    peak: float | None = None,
    is_holding: bool = False,
) -> dict:
    """综合技术与消息面，给出单只股票的当日建议。

    stock_sent / sector_sent 可传入大模型打分；为 None 时回退词典法。
    sector 可传入自选股配置的板块；为 None 时从 config.STOCK_SECTOR 查。
    avg_cost / peak / is_holding：有持仓时启用止损覆盖与宽松卖出门槛。
    """
    if sector is None:
        sector = config.STOCK_SECTOR.get(symbol, config.STOCK_SECTOR.get(name, "综合"))
    info = strategy.latest_signal(df) or {}
    tech = int(info.get("signal", 0))
    close = info.get("close")

    if stock_sent is None:
        stock_sent = sentiment.score_news(stock_news_texts)
    if sector_sent is None:
        impacts = sentiment.sector_impacts(macro_texts)
        sector_sent = sentiment.sentiment_for_sector(impacts, sector)

    # 统一加权综合得分：技术面 + 个股消息 + 板块时政（W_TECH 真正生效）
    news_score = config.W_STOCK_NEWS * stock_sent + config.W_MACRO * sector_sent  # 消息面分量
    combined = config.W_TECH * tech + news_score
    combined = round(combined, 3)

    sell_th = (
        config.HOLDING_SELL_THRESHOLD if is_holding or avg_cost
        else config.SELL_THRESHOLD
    )
    reasons = [
        f"技术面: 均线{'金叉' if tech == 1 else '死叉' if tech == -1 else '无交叉'}",
        f"个股消息: {stock_sent:+.2f}",
        f"板块[{sector}]时政情绪: {sector_sent:+.2f}",
        f"综合得分: {combined:+.2f} (买入≥{config.BUY_THRESHOLD:+} / 卖出≤{sell_th:+})",
    ]

    risk_triggers: list[str] = []
    if avg_cost and close:
        risk_triggers = risk.check_exit(float(avg_cost), float(close), peak)

    if risk_triggers:
        action = "必须卖出"
        conf = 95
        reasons.append("风控触发，覆盖综合分阈值 → 必须卖出")
        reasons.extend(risk_triggers)
    elif combined >= config.BUY_THRESHOLD:
        action = "买入"
        conf = min(90, 50 + int(combined * 100))
        reasons.append("综合得分达到买入阈值")
    elif combined <= sell_th:
        action = "卖出"
        conf = min(90, 50 + int(abs(combined) * 100))
        if is_holding or avg_cost:
            reasons.append(
                f"持仓卖出：综合得分 ≤ 持仓卖出门槛 {config.HOLDING_SELL_THRESHOLD:+}"
            )
        else:
            reasons.append("综合得分达到卖出阈值")
    elif stock_sent >= 0.3:
        action = "关注"
        conf = 50
        reasons.append("未达阈值，但个股消息面强正面，可关注")
    elif stock_sent <= -0.3:
        action = "观望"
        conf = 50
        reasons.append("未达阈值，且消息面偏空")
    else:
        action = "持有/观望"
        conf = 30
        reasons.append("技术与消息面均无明确信号")

    detail = {
        "close": close,
        "ma_short": info.get("ma_short"),
        "ma_long": info.get("ma_long"),
        "tech": tech,
        "stock_sent": stock_sent,
        "sector_sent": sector_sent,
        "news_score": round(news_score, 3),
        "combined": combined,
        "sell_threshold_used": sell_th,
        "is_holding": bool(is_holding or avg_cost),
        "risk_triggers": risk_triggers,
    }
    if avg_cost:
        detail["avg_cost"] = float(avg_cost)
        detail.update(risk.exit_price(float(avg_cost)))

    return {
        "symbol": symbol,
        "name": name,
        "action": action,
        "confidence": conf,
        "reasons": reasons,
        "detail": detail,
        "must_sell": action == "必须卖出",
    }


def format_recommendation(rec: dict) -> str:
    d = rec["detail"]
    lines = [
        f"{rec['name']}({rec['symbol']}) => {rec['action']}  [置信度 {rec['confidence']}%]",
        f"  收盘 {d['close']} | MA{config.SHORT_MA}={d['ma_short']} MA{config.LONG_MA}={d['ma_long']}",
    ]
    lines += [f"  - {r}" for r in rec["reasons"]]
    return "\n".join(lines)
