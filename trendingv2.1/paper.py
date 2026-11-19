# -*- coding: utf-8 -*-
"""
模拟盘（paper trading）：根据买卖建议自动执行虚拟交易，持仓持久化到本地 JSON。

这是「自动买卖」的安全演练版——无需券商接口，完全在本地模拟。
不接真实券商；真实持仓仅由日报给出 must-sell 建议。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import config
import risk
from costs import commission, stamp_tax
from signals import SELL_ACTIONS

ACCOUNT_FILE = Path(__file__).parent / "paper_account.json"
TRADES_FILE = Path(__file__).parent / "paper_trades.csv"
EQUITY_FILE = Path(__file__).parent / "paper_equity.csv"


class PaperAccount:
    def __init__(self) -> None:
        self.cash: float = config.INITIAL_CAPITAL
        self.positions: dict[str, dict] = {}  # symbol -> {shares, avg_cost}
        self.signals: dict[str, str] = {}     # symbol -> "action|signal_date"（去重）
        self.trades: list[dict] = []
        self._load()

    # ---------- 持久化 ----------
    def _load(self) -> None:
        if ACCOUNT_FILE.exists():
            try:
                data = json.loads(ACCOUNT_FILE.read_text(encoding="utf-8"))
                self.cash = float(data.get("cash", config.INITIAL_CAPITAL))
                self.positions = data.get("positions", {})
                self.signals = data.get("signals", {})
            except Exception:  # noqa: BLE001
                pass

    def save(self) -> None:
        ACCOUNT_FILE.write_text(
            json.dumps({"cash": self.cash, "positions": self.positions, "signals": self.signals},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_equity(self, prices: dict[str, float], as_of: str | None = None) -> float:
        """追加一条盯市权益曲线（按日去重：同一 as_of 只保留最新一条）。返回当日权益。"""
        as_of = as_of or datetime.now().strftime("%Y-%m-%d")
        eq = round(self.equity(prices), 2)
        rows: list[str] = []
        if EQUITY_FILE.exists():
            try:
                lines = EQUITY_FILE.read_text(encoding="utf-8-sig").strip().splitlines()
                if lines and lines[0].startswith("date,"):
                    rows = lines[1:]
                else:
                    rows = lines
                rows = [ln for ln in rows if not ln.startswith(f"{as_of},")]
            except Exception:  # noqa: BLE001
                rows = []
        n_pos = len(self.positions)
        rows.append(f"{as_of},{self.cash:.2f},{eq:.2f},{n_pos}")
        EQUITY_FILE.write_text(
            "date,cash,equity,positions\n" + "\n".join(rows) + "\n",
            encoding="utf-8-sig",
        )
        return eq

    # ---------- 交易 ----------
    def buy(self, symbol: str, name: str, price: float) -> str | None:
        if price is None or price <= 0:
            return None
        buy_price = price * (1 + config.SLIPPAGE)
        # 目标仓位：按账户权益等权（POSITION_PCT），而非全仓
        equity = self.cash + sum(p["shares"] * p["avg_cost"] for p in self.positions.values())
        target_value = equity * config.POSITION_PCT
        pos = self.positions.get(symbol, {"shares": 0, "avg_cost": 0.0})
        budget = target_value - pos["shares"] * buy_price
        if budget <= 0:
            return f"{name} 已达目标仓位"
        lots = int(budget // (buy_price * (1 + config.COMMISSION_RATE)) // 100 * 100)
        if lots <= 0:
            return f"资金/仓位不足买入一手 {name}"
        cost = lots * buy_price + commission(lots * buy_price)
        if cost > self.cash:  # 现金不足时按现金缩减
            lots = int(self.cash // (buy_price * (1 + config.COMMISSION_RATE)) // 100 * 100)
            if lots <= 0:
                return f"现金不足买入一手 {name}"
            cost = lots * buy_price + commission(lots * buy_price)
        self.cash -= cost
        total_cost = pos["shares"] * pos["avg_cost"] + cost
        pos["shares"] += lots
        pos["avg_cost"] = total_cost / pos["shares"]
        pos["peak"] = max(pos.get("peak", 0.0), buy_price)
        self.positions[symbol] = pos
        self._record(symbol, name, "买入", lots, buy_price)
        return None

    def sell(self, symbol: str, name: str, price: float) -> str | None:
        pos = self.positions.get(symbol)
        if not pos or pos["shares"] <= 0:
            return f"{name} 无持仓，跳过卖出"  # 空仓
        if price is None or price <= 0:
            return None
        sell_price = price * (1 - config.SLIPPAGE)
        shares = pos["shares"]
        gross = shares * sell_price
        proceeds = gross - commission(gross) - stamp_tax(gross)
        self.cash += proceeds
        self.positions.pop(symbol, None)
        self._record(symbol, name, "卖出", shares, sell_price)
        return None

    def _record(self, symbol: str, name: str, side: str, shares: int, price: float) -> None:
        row = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "name": name,
            "side": side,
            "shares": shares,
            "price": round(price, 3),
        }
        self.trades.append(row)
        header = not TRADES_FILE.exists()
        with TRADES_FILE.open("a", encoding="utf-8-sig") as f:
            if header:
                f.write("time,symbol,name,side,shares,price\n")
            f.write(f"{row['time']},{symbol},{name},{side},{shares},{row['price']}\n")

    # ---------- 风控卖出 ----------
    def update_peak(self, symbol: str, price: float) -> None:
        pos = self.positions.get(symbol)
        if pos and price:
            pos["peak"] = max(pos.get("peak", pos["avg_cost"]), price)

    def apply_risk_exits(self, symbol: str, name: str, price: float) -> list[str]:
        """按止损/止盈/移动止损规则检查持仓，触发则卖出。返回提醒列表。"""
        pos = self.positions.get(symbol)
        if not pos or pos["shares"] <= 0:
            return []
        self.update_peak(symbol, price)
        triggers = risk.check_exit(pos["avg_cost"], price, pos.get("peak"))
        if triggers:
            self.sell(symbol, name, price)
            return [f"{name} 触发卖出 → " + "；".join(triggers)]
        return []

    def trade(self, rec: dict, price: float, signal_date: str = "") -> list[str]:
        """根据建议执行买入/卖出；同一 signal_date 同一动作只执行一次。返回说明列表。"""
        symbol, name = rec["symbol"], rec["name"]
        action = rec["action"]
        # 必须卖出与卖出共用去重键，避免同日风控后再被信号卖一次
        norm = "卖出" if action in SELL_ACTIONS else action
        key = f"{norm}|{signal_date}"
        if action in ("买入", *SELL_ACTIONS) and self.signals.get(symbol) == key:
            return [f"{name} 该信号（{signal_date}）已执行过，跳过"]
        if action == "买入":
            err = self.buy(symbol, name, price)
            if err is None and signal_date:
                self.signals[symbol] = key
            return [err or f"模拟买入 {name}"]
        if action in SELL_ACTIONS:
            err = self.sell(symbol, name, price)
            if err is None and signal_date:
                self.signals[symbol] = key
            label = "必须卖出" if action == "必须卖出" else "卖出"
            return [err or f"模拟{label} {name}"]
        return [f"模拟盘无操作（{action}）"]

    # ---------- 状态 ----------
    def equity(self, prices: dict[str, float]) -> float:
        total = self.cash
        for symbol, pos in self.positions.items():
            total += pos["shares"] * prices.get(symbol, pos["avg_cost"])
        return total

    def summary(self, prices: dict[str, float]) -> str:
        lines = ["模拟盘持仓："]
        if not self.positions:
            lines.append("  （空仓）")
        for symbol, pos in self.positions.items():
            px = prices.get(symbol, pos["avg_cost"])
            pnl = (px - pos["avg_cost"]) / pos["avg_cost"] * 100
            ep = risk.exit_price(pos["avg_cost"])
            lines.append(
                f"  {symbol}: {pos['shares']}股 成本{pos['avg_cost']:.2f} "
                f"现价{px:.2f} 盈亏{pnl:+.2f}%"
            )
            lines.append(f"    止损价 {ep['stop_loss']} | 止盈价 {ep['take_profit']}")
        lines.append(f"  现金: {self.cash:,.0f} | 总资产: {self.equity(prices):,.0f}")
        return "\n".join(lines)
