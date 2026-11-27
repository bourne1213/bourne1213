# trendingv2.1

A-share **local helper**: dual-MA + news sentiment → buy/sell/must-sell advice → **paper trading** + **advisory must-sell** for real holdings (`holdings.json`).

**No live broker trading.** Paper only executes locally; real holdings never auto-sell.

This directory is a **standalone** project (`config.PROJECT_NAME = "trendingv2.1"`). It does **not** depend on, replace, or share runtime state with any older helper path (for example a separate `trading/` tree). Run and develop only under `trendingv2.1/`.

## Quick start

```bash
cd trendingv2.1
pip install -r requirements.txt
cp watchlist.example.json watchlist.json
cp holdings.example.json holdings.json   # optional
python runner.py --daily --demo --no-screener
python app.py                            # http://127.0.0.1:5000
python -m unittest discover -s tests -v
```

## Switch API providers

### In the web board (recommended)

1. Start `python app.py` → open http://127.0.0.1:5000
2. Use the **供应商设置** panel at the top (also linked from the header button)
3. Pick **行情 / 大模型 / 新闻**, optionally fill OpenAI-compatible URL or HTTP K-line keys
4. Click **保存供应商** → settings write to local `providers.json` and apply in-process
5. **刷新今日分析** uses the new providers immediately (no Flask restart)

`providers.json` is gitignored. Secrets can stay in env vars; the panel only overrides what you save.

### Env / config (CLI & defaults)

Same knobs work via environment variables or `config.py`. Local `providers.json` (from the UI) **overrides** env when present.

| Subsystem | Env / config | Values |
|-----------|--------------|--------|
| Market | `MARKET_PROVIDER` | `auto` (default) \| `eastmoney` \| `sina` \| `fuyao` \| `tonghuashun` |
| Market chain | `MARKET_PROVIDER_CHAIN` | default `fuyao,tonghuashun,eastmoney,sina` (skip sources missing keys) |
| Fuyao | `FUYAO_API_KEY`, `FUYAO_BASE_URL` | optional HTTP K-line adapter |
| 同花顺 | `TONGHUASHUN_API_KEY`, `TONGHUASHUN_BASE_URL` | optional HTTP K-line adapter |
| LLM | `LLM_PROVIDER` | `auto` \| `openai` \| `dictionary` |
| LLM OpenAI-compatible | `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_PROXY` | DeepSeek / OpenAI / any compatible gateway |
| News | `NEWS_SOURCES` | comma list `cctv,cls,stock_em` \| `none` to disable |

### Examples

```bash
# Default: eastmoney → sina (Fuyao/同花顺 skipped until keys set)
python runner.py --daily

# Pin东财 only
export MARKET_PROVIDER=eastmoney

# Optional HTTP K-line (Fuyao or 同花顺), then fall through
export FUYAO_API_KEY=...
export FUYAO_BASE_URL=https://your-gateway.example/v1
export TONGHUASHUN_API_KEY=...
export TONGHUASHUN_BASE_URL=https://your-ths-gateway.example/v1

# OpenAI-compatible LLM (else auto → dictionary fallback, shown on the board)
export LLM_API_KEY=sk-...
export LLM_BASE_URL=https://api.deepseek.com/v1
export LLM_MODEL=deepseek-chat
# Force dictionary (no network LLM):
export LLM_PROVIDER=dictionary

# News: only财联社 + 个股东财; or turn news off
export NEWS_SOURCES=cls,stock_em
export NEWS_SOURCES=none
```

HTTP K-line contract (Fuyao / 同花顺 adapters):

```
GET {BASE_URL}/kline?symbol=600519&period=daily&adjust=qfq
Authorization: Bearer {API_KEY}
→ {"data":[{"date":"2024-01-02","open":..,"high":..,"low":..,"close":..,"volume":..}, ...]}
```

Chinese field aliases (`日期/开盘/最高/最低/收盘/成交量`) are accepted. Failures fall through the chain; the dashboard status chips show which provider won or that LLM/news fell back.

Implementation lives under `providers/` (`market.py`, `llm_providers.py`, `news_providers.py`). Add a new market adapter by registering it in `providers.market.REGISTRY` and documenting the env keys here.

## Risk / execution

| Rule | Behavior |
|------|----------|
| Stop-loss / take-profit | Action → **必须卖出**; paper auto-sells; real holdings advisory only |
| Holding sell threshold | `HOLDING_SELL_THRESHOLD=-0.12` (vs `SELL_THRESHOLD=-0.25`) |
| Web ↔ CLI | `WEB_REPORT_TRADE` / `REPORT_TRADE_DEFAULT` default **True** |
| Equity history | Daily append to `paper_equity.csv` |
| Data harden | HTML / bad JSON caught; optional Fuyao / 同花顺 HTTP adapters |
| Sector | `600664` → 医药 |

## Layout

| File | Role |
|------|------|
| `config.py` | Thresholds, weights, risk, provider env knobs, `PROJECT_NAME` |
| `settings_store.py` | Dashboard `providers.json` overrides (apply without restart) |
| `providers/` | Market / LLM / news adapters + status |
| `runner.py` / `app.py` | CLI / Flask board (`/api/providers`) |
| `report.py` | Daily pipeline + `holdings_risk` + provider status |
| `signals.py` / `risk.py` / `paper.py` | Advice, exits, paper + equity curve |
| `data.py` / `screener.py` | Quotes + screen |
| `holdings.py` / `watchlist.py` | Real positions / watchlist |

## Disclaimer

For research/learning only. Not investment advice. Paper results ≠ future performance.
