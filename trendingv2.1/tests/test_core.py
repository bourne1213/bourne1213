from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import app
import config
import data
import factors
import paper
import portfolio_backtest
import report
import risk
import screener
import signals
import watchlist


class FactorTests(unittest.TestCase):
    def test_deeper_drawdown_receives_lower_score(self) -> None:
        common = {
            "trend": 0.0,
            "rs": 0.0,
            "mid_mom": 0.0,
            "short_mom": 0.0,
            "vol": 0.1,
            "liq": 1.0,
        }
        ranks = factors.cross_section_rank({
            "stable": {**common, "dd": 0.02},
            "crashed": {**common, "dd": 0.40},
        })
        scores = factors.composite_score(ranks, None)
        self.assertGreater(scores["stable"], scores["crashed"])

    def test_constraints_drop_overheated_and_below_threshold(self) -> None:
        picks = [
            {"symbol": "hot", "final_score": 0.90, "ret5_pct": 25,
             "news_score": 0, "ranks": {"trend": 0.9, "rs": 0.9}, "sector": "A"},
            {"symbol": "ok", "final_score": 0.70, "ret5_pct": 5,
             "news_score": 0, "ranks": {"trend": 0.6, "rs": 0.6}, "sector": "A"},
            {"symbol": "weak", "final_score": 0.40, "ret5_pct": 1,
             "news_score": 0, "ranks": {"trend": 0.8, "rs": 0.8}, "sector": "B"},
        ]
        self.assertEqual([p["symbol"] for p in report._apply_constraints(picks)], ["ok"])

    def test_market_regime_risk_gate(self) -> None:
        dates = pd.bdate_range("2025-01-01", periods=80)
        close = pd.Series(np.linspace(120, 90, len(dates)))
        bench = pd.DataFrame({"date": dates, "close": close})
        self.assertEqual(screener.market_regime(bench), "risk")


class StorageTests(unittest.TestCase):
    def test_empty_watchlist_does_not_restore_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "watchlist.json"
            path.write_text("[]", encoding="utf-8")
            with mock.patch.object(watchlist, "WATCHLIST_FILE", path):
                self.assertEqual(watchlist.load(), [])


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.app.config.update(TESTING=True)
        self.client = app.app.test_client()

    def test_watchlist_item_updates_are_server_side(self) -> None:
        state: list[dict] = []

        def load() -> list[dict]:
            return copy.deepcopy(state)

        def save(items: list[dict]) -> None:
            state[:] = copy.deepcopy(items)

        with mock.patch.object(app.watchlist, "load", side_effect=load), \
                mock.patch.object(app.watchlist, "save", side_effect=save):
            first = self.client.post("/api/watchlist/item", json={
                "symbol": "600519", "name": "A", "sector": "消费",
            })
            second = self.client.post("/api/watchlist/item", json={
                "symbol": "000001", "name": "B", "sector": "银行",
            })
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)
            self.assertEqual({item["symbol"] for item in state}, {"600519", "000001"})

            deleted = self.client.delete(
                "/api/watchlist/item", json={"symbol": "600519"}
            )
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual([item["symbol"] for item in state], ["000001"])

    def test_holding_rejects_invalid_values(self) -> None:
        for shares, cost in ((-100, 10), (100, 0), (1.5, 10)):
            response = self.client.post("/api/holdings/item", json={
                "symbol": "600519",
                "name": "A",
                "shares": shares,
                "cost_price": cost,
            })
            self.assertEqual(response.status_code, 400)

    def test_partial_valuation_keeps_total_cost(self) -> None:
        positions = [
            {"symbol": "600519", "name": "A", "shares": 100, "cost_price": 10},
            {"symbol": "000001", "name": "B", "shares": 100, "cost_price": 20},
        ]
        quotes = {"600519": {"name": "A", "price": 11}}
        with mock.patch.object(app.holdings, "load", return_value=positions), \
                mock.patch.object(app, "_get_snap_map", return_value=quotes):
            data = self.client.get("/api/pnl").get_json()
        self.assertEqual(data["total_cost"], 3000.0)
        self.assertEqual(data["unpriced_cost"], 2000.0)
        self.assertTrue(data["partial"])


class RiskOverrideTests(unittest.TestCase):
    """止损覆盖综合分 + 持仓宽松卖出门槛。"""

    @staticmethod
    def _flat_df(close: float = 8.21, days: int = 40) -> pd.DataFrame:
        """无交叉的平稳行情（tech=0）。"""
        dates = pd.bdate_range(end=pd.Timestamp("2026-08-25"), periods=days)
        return pd.DataFrame({
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        })

    def test_check_exit_stop_loss(self) -> None:
        # 哈药：成本 8.9811，现价 8.21 → -8.59% < -8%
        triggers = risk.check_exit(8.9811, 8.21)
        self.assertTrue(triggers)
        self.assertTrue(any("止损" in t for t in triggers))
        self.assertTrue(risk.must_sell(8.9811, 8.21))

    def test_stop_overrides_combined_score(self) -> None:
        # combined 约 -0.18（高于 SELL_THRESHOLD=-0.25）但止损已破 → 必须卖出
        df = self._flat_df(8.21)
        with mock.patch.object(signals.sentiment, "score_news", return_value=-0.60), \
                mock.patch.object(signals.sentiment, "sector_impacts", return_value={}), \
                mock.patch.object(signals.sentiment, "sentiment_for_sector", return_value=0.0):
            rec = signals.recommend(
                "600664", "哈药股份", df, ["偏空"], [],
                stock_sent=-0.60, sector_sent=0.0, sector="医药",
                avg_cost=8.9811, is_holding=True,
            )
        self.assertEqual(rec["action"], "必须卖出")
        self.assertTrue(rec["must_sell"])
        self.assertGreater(rec["detail"]["combined"], config.SELL_THRESHOLD)

    def test_holding_soft_sell_threshold(self) -> None:
        # 未破止损，但 combined=-0.18 ≤ HOLDING_SELL_THRESHOLD(-0.12) → 卖出
        df = self._flat_df(8.60)  # 相对成本约 -4%，未破 -8%
        with mock.patch.object(signals.sentiment, "score_news", return_value=-0.60):
            rec = signals.recommend(
                "600664", "哈药股份", df, [], [],
                stock_sent=-0.60, sector_sent=0.0, sector="医药",
                avg_cost=8.9811, is_holding=True,
            )
        self.assertEqual(rec["action"], "卖出")
        self.assertFalse(rec["must_sell"])
        self.assertEqual(rec["detail"]["sell_threshold_used"], config.HOLDING_SELL_THRESHOLD)

    def test_non_holding_keeps_strict_sell(self) -> None:
        df = self._flat_df(8.60)
        rec = signals.recommend(
            "600664", "哈药股份", df, [], [],
            stock_sent=-0.60, sector_sent=0.0, sector="医药",
            is_holding=False,
        )
        # combined = 0.5*0 + 0.3*(-0.6) = -0.18 > -0.25 → 非持仓不卖
        self.assertNotIn(rec["action"], signals.SELL_ACTIONS)
        self.assertEqual(rec["detail"]["sell_threshold_used"], config.SELL_THRESHOLD)

    def test_paper_risk_exit_and_equity_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            acct_file = tmp / "paper_account.json"
            trades_file = tmp / "paper_trades.csv"
            equity_file = tmp / "paper_equity.csv"
            with mock.patch.object(paper, "ACCOUNT_FILE", acct_file), \
                    mock.patch.object(paper, "TRADES_FILE", trades_file), \
                    mock.patch.object(paper, "EQUITY_FILE", equity_file):
                acct = paper.PaperAccount()
                acct.cash = 100_000.0
                acct.positions = {
                    "600664": {"shares": 100, "avg_cost": 8.9811, "peak": 9.0},
                }
                notes = acct.apply_risk_exits("600664", "哈药股份", 8.21)
                self.assertTrue(notes)
                self.assertNotIn("600664", acct.positions)
                eq = acct.append_equity({"600664": 8.21}, as_of="2026-08-25")
                self.assertAlmostEqual(eq, acct.cash, places=2)
                text = equity_file.read_text(encoding="utf-8-sig")
                self.assertIn("2026-08-25", text)
                self.assertIn("date,cash,equity,positions", text)

    def test_html_payload_detected(self) -> None:
        self.assertTrue(data.looks_like_html("<!DOCTYPE html><html>"))
        with self.assertRaises(RuntimeError):
            data.ensure_json_payload("<html>blocked</html>", "测试源")

    def test_watchlist_sector_ignores_其他(self) -> None:
        items = [{"symbol": "600664", "name": "哈药股份", "sector": "其他"}]
        m = watchlist.sector_map(items)
        self.assertEqual(m["600664"], "医药")


class PortfolioBacktestTests(unittest.TestCase):
    @staticmethod
    def _history(dates: pd.DatetimeIndex, start: float, end: float) -> pd.DataFrame:
        close = np.linspace(start, end, len(dates))
        return pd.DataFrame({
            "date": dates,
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(len(dates), 1_000_000.0),
        })

    def test_trades_and_marks_through_missing_stock_date(self) -> None:
        dates = pd.bdate_range("2025-01-02", periods=140)
        rising = self._history(dates, 10.0, 20.0).drop(index=110).reset_index(drop=True)
        falling = self._history(dates, 20.0, 10.0)
        histories = {"600001": rising, "000001": falling}
        benchmark = pd.DataFrame({"date": dates, "close": np.linspace(100, 110, len(dates))})

        with mock.patch.object(
            portfolio_backtest, "_fetch_universe",
            return_value=[("600001", "上涨股"), ("000001", "下跌股")],
        ), mock.patch.object(
            portfolio_backtest.screener, "fetch_hist",
            side_effect=lambda code: histories[code],
        ), mock.patch.object(
            portfolio_backtest, "_fetch_benchmarks",
            return_value=(benchmark, benchmark.copy()),
        ):
            result = portfolio_backtest.run(
                universe_size=2,
                top_n=1,
                lookback_days=100,
                rebalance_days=5,
                initial_capital=100_000,
            )

        self.assertNotIn("error", result)
        equity = result["equity"]
        self.assertTrue(np.isfinite(equity).all())
        self.assertGreater(equity.min(), 50_000)
        self.assertGreater(result["rebalance_count"], 0)


if __name__ == "__main__":
    unittest.main()
