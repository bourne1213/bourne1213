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

## Risk / execution

| Rule | Behavior |
|------|----------|
| Stop-loss / take-profit | Action → **必须卖出**; paper auto-sells; real holdings advisory only |
| Holding sell threshold | `HOLDING_SELL_THRESHOLD=-0.12` (vs `SELL_THRESHOLD=-0.25`) |
| Web ↔ CLI | `WEB_REPORT_TRADE` / `REPORT_TRADE_DEFAULT` default **True** |
| Equity history | Daily append to `paper_equity.csv` |
| Data harden | HTML / bad JSON caught; optional Fuyao stub if `FUYAO_API_KEY` set |
| Sector | `600664` → 医药 |

## Layout

| File | Role |
|------|------|
| `config.py` | Thresholds, weights, risk, `PROJECT_NAME` |
| `runner.py` / `app.py` | CLI / Flask board |
| `report.py` | Daily pipeline + `holdings_risk` |
| `signals.py` / `risk.py` / `paper.py` | Advice, exits, paper + equity curve |
| `data.py` / `screener.py` | Quotes + screen |
| `holdings.py` / `watchlist.py` | Real positions / watchlist |

## Disclaimer

For research/learning only. Not investment advice. Paper results ≠ future performance.
