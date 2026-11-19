# bourne1213

## A-share trading helper (`trading/`)

Local A-share decision helper: dual-MA + news sentiment → buy/sell advice, **paper trading**, and **advisory must-sell** for real holdings (`holdings.json`). No live broker integration.

```bash
cd trading
pip install -r requirements.txt
python runner.py --daily --demo          # offline smoke
python runner.py --daily                 # full daily (paper + risk)
python app.py                            # web board http://127.0.0.1:5000
python -m unittest discover -s tests -v
```

Copy `watchlist.example.json` → `watchlist.json` and `holdings.example.json` → `holdings.json` to start. See `trading/README.md` and project docs for strategy details.
