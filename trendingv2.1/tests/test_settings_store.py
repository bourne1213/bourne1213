# -*- coding: utf-8 -*-
"""Tests for in-app provider settings (providers.json + API)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app
import config
import settings_store


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "providers.json"
        self.patcher = mock.patch.object(settings_store, "SETTINGS_FILE", self.path)
        self.patcher.start()
        # Reset config to baseline before each test
        settings_store.apply({})

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_merge_applies_without_restart(self) -> None:
        settings_store.merge_and_save({
            "MARKET_PROVIDER": "sina",
            "LLM_PROVIDER": "dictionary",
            "NEWS_SOURCES": "cls",
        })
        self.assertEqual(config.MARKET_PROVIDER, "sina")
        self.assertEqual(config.LLM_PROVIDER, "dictionary")
        self.assertEqual(config.NEWS_SOURCES, "cls")
        self.assertTrue(self.path.exists())

        # Re-apply from disk (simulates next /api/report)
        config.MARKET_PROVIDER = "auto"
        settings_store.apply()
        self.assertEqual(config.MARKET_PROVIDER, "sina")

    def test_secret_blank_keeps_existing(self) -> None:
        settings_store.merge_and_save({"LLM_API_KEY": "sk-keep"})
        self.assertEqual(config.LLM_API_KEY, "sk-keep")
        settings_store.merge_and_save({"LLM_PROVIDER": "openai", "LLM_API_KEY": ""})
        self.assertEqual(config.LLM_API_KEY, "sk-keep")
        self.assertEqual(config.LLM_PROVIDER, "openai")

    def test_reset_clears_file(self) -> None:
        settings_store.merge_and_save({"MARKET_PROVIDER": "eastmoney"})
        self.assertTrue(self.path.exists())
        settings_store.save_raw({})
        settings_store.apply({})
        self.assertFalse(self.path.exists())
        self.assertEqual(config.MARKET_PROVIDER, settings_store._BASELINE["MARKET_PROVIDER"])

    def test_public_view_masks_secrets(self) -> None:
        settings_store.merge_and_save({"LLM_API_KEY": "sk-abcdefghijk"})
        view = settings_store.public_view()
        self.assertTrue(view["effective"]["LLM_API_KEY_set"])
        self.assertNotIn("sk-abcdefghijk", view["effective"]["LLM_API_KEY"])
        self.assertIn("••••", view["effective"]["LLM_API_KEY"])


class ProvidersApiTests(unittest.TestCase):
    def setUp(self) -> None:
        app.app.config.update(TESTING=True)
        self.client = app.app.test_client()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "providers.json"
        self.patcher = mock.patch.object(settings_store, "SETTINGS_FILE", self.path)
        self.patcher.start()
        settings_store.apply({})

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_get_and_post_providers(self) -> None:
        got = self.client.get("/api/providers").get_json()
        self.assertTrue(got["ok"])
        self.assertIn("catalog", got)
        self.assertIn("market_providers", got["catalog"])

        saved = self.client.post("/api/providers", json={
            "MARKET_PROVIDER": "eastmoney",
            "LLM_PROVIDER": "dictionary",
            "NEWS_SOURCES": "none",
        }).get_json()
        self.assertTrue(saved.get("saved"))
        self.assertEqual(config.MARKET_PROVIDER, "eastmoney")
        self.assertEqual(config.LLM_PROVIDER, "dictionary")
        self.assertEqual(config.NEWS_SOURCES, "none")

        again = self.client.get("/api/providers").get_json()
        self.assertEqual(again["effective"]["MARKET_PROVIDER"], "eastmoney")
        self.assertEqual(again["active"]["llm_resolved"], "dictionary")

    def test_reset_via_api(self) -> None:
        self.client.post("/api/providers", json={"MARKET_PROVIDER": "sina"})
        self.assertEqual(config.MARKET_PROVIDER, "sina")
        self.client.post("/api/providers", json={"reset": True})
        self.assertEqual(config.MARKET_PROVIDER, settings_store._BASELINE["MARKET_PROVIDER"])
        self.assertFalse(self.path.exists())

    def test_providers_test_endpoint(self) -> None:
        self.client.post("/api/providers", json={"LLM_PROVIDER": "dictionary"})
        data = self.client.post("/api/providers/test", json={"target": "all"}).get_json()
        self.assertTrue(data["ok"])
        self.assertIn("llm", data["results"])
        self.assertIn("market", data["results"])
        self.assertIn("news", data["results"])


if __name__ == "__main__":
    unittest.main()
