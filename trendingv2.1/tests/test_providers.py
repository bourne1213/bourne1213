# -*- coding: utf-8 -*-
"""Focused tests for pluggable market / LLM / news provider selection & fallback."""
from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

import config
import data
import llm
import news
from providers import llm_providers, market, news_providers


def _ohlcv(n: int = 5) -> pd.DataFrame:
    dates = pd.bdate_range(end=pd.Timestamp("2026-08-25"), periods=n)
    return pd.DataFrame({
        "date": dates,
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.2,
        "volume": 1_000_000.0,
    })


class MarketProviderTests(unittest.TestCase):
    def test_auto_chain_skips_unkeyed_http_sources(self) -> None:
        with mock.patch.object(config, "MARKET_PROVIDER", "auto"), \
                mock.patch.object(config, "MARKET_PROVIDER_CHAIN", "fuyao,tonghuashun,eastmoney,sina"), \
                mock.patch.object(config, "FUYAO_API_KEY", ""), \
                mock.patch.object(config, "TONGHUASHUN_API_KEY", ""):
            chain = market.resolve_chain()
        keys = [k for _, _, k in chain]
        self.assertEqual(keys, ["eastmoney", "sina"])

    def test_pin_eastmoney(self) -> None:
        with mock.patch.object(config, "MARKET_PROVIDER", "eastmoney"):
            chain = market.resolve_chain()
        self.assertEqual(len(chain), 1)
        self.assertEqual(chain[0][2], "eastmoney")

    def test_fuyao_ready_included_in_auto(self) -> None:
        with mock.patch.object(config, "MARKET_PROVIDER", "auto"), \
                mock.patch.object(config, "MARKET_PROVIDER_CHAIN", "fuyao,eastmoney"), \
                mock.patch.object(config, "FUYAO_API_KEY", "k"), \
                mock.patch.object(config, "FUYAO_BASE_URL", "https://example.test"):
            chain = market.resolve_chain()
        self.assertEqual([k for _, _, k in chain], ["fuyao", "eastmoney"])

    def test_fetch_falls_through_and_records_winner(self) -> None:
        boom = RuntimeError("fuyao down")
        with mock.patch.object(config, "MARKET_PROVIDER", "auto"), \
                mock.patch.object(config, "MARKET_PROVIDER_CHAIN", "fuyao,eastmoney"), \
                mock.patch.object(config, "FUYAO_API_KEY", "k"), \
                mock.patch.object(config, "FUYAO_BASE_URL", "https://example.test"), \
                mock.patch.object(market, "fetch_fuyao", side_effect=boom), \
                mock.patch.object(market, "fetch_eastmoney", return_value=_ohlcv()), \
                mock.patch.object(data, "_apply_proxy_policy"):
            df = market.fetch("600519", retry_times=1, delay=0)
        self.assertEqual(len(df), 5)
        st = market.get_last_status()
        self.assertTrue(st.ok)
        self.assertEqual(st.provider, "eastmoney")

    def test_http_kline_normalizes_chinese_fields(self) -> None:
        payload = {
            "data": [
                {"日期": "2026-01-02", "开盘": 1, "最高": 2, "最低": 0.5, "收盘": 1.5, "成交量": 100},
                {"日期": "2026-01-03", "开盘": 1.5, "最高": 2.2, "最低": 1.4, "收盘": 2.0, "成交量": 120},
            ]
        }
        fake_resp = mock.Mock()
        fake_resp.raise_for_status = mock.Mock()
        fake_resp.json = mock.Mock(return_value=payload)
        fake_resp.text = ""
        with mock.patch.object(config, "START_DATE", "20220101"), \
                mock.patch("providers.market.requests.get", return_value=fake_resp):
            df = market.fetch_http_kline(
                "600519",
                base_url="https://example.test/api",
                api_key="secret",
                label="同花顺",
            )
        self.assertEqual(list(df.columns), ["date", "open", "high", "low", "close", "volume"])
        self.assertEqual(len(df), 2)
        self.assertAlmostEqual(float(df.iloc[-1]["close"]), 2.0)


class LlmProviderTests(unittest.TestCase):
    def test_auto_without_key_uses_dictionary(self) -> None:
        with mock.patch.object(config, "LLM_PROVIDER", "auto"), \
                mock.patch.object(config, "LLM_API_KEY", ""):
            self.assertEqual(llm_providers.resolve_provider(), "dictionary")
            self.assertFalse(llm.available())

    def test_force_dictionary_even_with_key(self) -> None:
        with mock.patch.object(config, "LLM_PROVIDER", "dictionary"), \
                mock.patch.object(config, "LLM_API_KEY", "sk-test"):
            self.assertEqual(llm.resolve_provider(), "dictionary")
            self.assertFalse(llm.available())
            result = llm.analyze_news(["利好降准"], {"600519": ["回购"]})
            self.assertIsNone(result)
            st = llm.get_last_status()
            self.assertEqual(st.provider, "dictionary")
            self.assertEqual(st.fallback, "dictionary")

    def test_openai_fallback_on_chat_failure(self) -> None:
        with mock.patch.object(config, "LLM_PROVIDER", "openai"), \
                mock.patch.object(config, "LLM_API_KEY", "sk-test"), \
                mock.patch.object(config, "LLM_BASE_URL", "https://example.test/v1"), \
                mock.patch.object(config, "LLM_MODEL", "demo-model"), \
                mock.patch.object(llm_providers, "_chat", side_effect=RuntimeError("boom")):
            result = llm.analyze_news(["宏观"], {"600519": ["新闻"]})
        self.assertIsNone(result)
        st = llm.get_last_status()
        self.assertFalse(st.ok)
        self.assertEqual(st.fallback, "dictionary")
        self.assertIn("词典法", st.detail)

    def test_openai_success_sets_status(self) -> None:
        payload = {
            "summary": "偏多",
            "sentiment": 0.4,
            "themes": [{"theme": "银行", "sentiment": 0.3, "reason": "降准"}],
            "stocks": {},
        }
        with mock.patch.object(config, "LLM_PROVIDER", "auto"), \
                mock.patch.object(config, "LLM_API_KEY", "sk-test"), \
                mock.patch.object(config, "LLM_BASE_URL", "https://example.test/v1"), \
                mock.patch.object(config, "LLM_MODEL", "demo-model"), \
                mock.patch.object(llm_providers, "_chat", return_value='{"summary":"偏多","sentiment":0.4,"themes":[],"stocks":{}}'):
            result = llm.analyze_news(["降准"], {})
        self.assertEqual(result["summary"], "偏多")
        st = llm.get_last_status()
        self.assertTrue(st.ok)
        self.assertEqual(st.provider, "openai")


class NewsProviderTests(unittest.TestCase):
    def test_parse_sources(self) -> None:
        with mock.patch.object(config, "NEWS_SOURCES", "cctv, cls ,stock_em"):
            self.assertEqual(news.parse_enabled_sources(), ["cctv", "cls", "stock_em"])
        with mock.patch.object(config, "NEWS_SOURCES", "none"):
            self.assertEqual(news.parse_enabled_sources(), [])

    def test_fetch_all_fail_soft(self) -> None:
        with mock.patch.object(config, "NEWS_SOURCES", "cctv,cls,stock_em"), \
                mock.patch.object(news_providers, "fetch_cctv", side_effect=RuntimeError("cctv down")), \
                mock.patch.object(news_providers, "fetch_cls", side_effect=RuntimeError("cls down")), \
                mock.patch.object(news_providers, "fetch_stock_em", side_effect=RuntimeError("em down")):
            result = news.fetch_all({"600519": "贵州茅台"})
        self.assertEqual(result["macro"], [])
        self.assertEqual(result["stocks"]["600519"], [])
        st = news.get_last_status()
        self.assertFalse(st.ok)
        self.assertEqual(st.fallback, "empty")

    def test_partial_macro_success(self) -> None:
        with mock.patch.object(config, "NEWS_SOURCES", "cctv,cls"), \
                mock.patch.object(news_providers, "fetch_cctv", side_effect=RuntimeError("down")), \
                mock.patch.object(news_providers, "fetch_cls", return_value=[
                    {"title": "t", "content": "降准利好", "time": "", "source": "财联社"},
                ]):
            result = news.fetch_all({})
        self.assertEqual(len(result["macro"]), 1)
        st = news.get_last_status()
        self.assertTrue(st.ok)
        self.assertIn("财联社", st.detail)


if __name__ == "__main__":
    unittest.main()
