# A股量化策略系统（技术面 + 消息面 + 模拟盘）

每天自动：抓行情 → 算均线信号 → 抓新闻/时政 → 情绪打分 → 融合出「买入/卖出/必须卖出/关注/观望」建议 → 模拟盘自动交易 → 推送报告。

**模拟盘只在本机虚拟交易，不碰真钱。** 真实持仓（`holdings.json`）仅产出止损/止盈 **必须卖出** 建议（advisory），不接券商、不自动减持。Web 与 CLI 默认都跑纸面风控（`WEB_REPORT_TRADE` / `REPORT_TRADE_DEFAULT`）；持仓卖出门槛见 `HOLDING_SELL_THRESHOLD`；权益曲线写入 `paper_equity.csv`。

## 目录结构

| 文件 | 作用 |
|------|------|
| `config.py` | 标的、均线参数、回测参数、推送配置、板块映射 |
| `data.py` | 拉行情（可选 Fuyao 桩 → 东财/新浪；HTML/坏 JSON 加固 / 离线 demo） |
| `strategy.py` | 双均线金叉/死叉信号 |
| `news.py` | 抓新闻：CCTV 时政 + 财联社快讯 + 个股新闻 |
| `sentiment.py` | 词典法情绪打分 + 政策→板块映射 |
| `llm.py` | 大模型语义分析（OpenAI 兼容，默认 DeepSeek；无 key 自动回退词典） |
| `signals.py` | 融合技术面+消息面；止损覆盖为「必须卖出」；持仓宽松卖出门槛 |
| `screener.py` | 全市场选股：筛出今天最值得买的 Top N |
| `risk.py` | 卖出规则：止损 / 止盈 / 移动止损（命中 → 必须卖出） |
| `costs.py` | 交易成本共用函数（佣金最低5元 / 印花税） |
| `sectors.py` | 板块关键词映射 + 名称→板块猜测 |
| `backtest.py` | 回测（含佣金+滑点），输出收益/回撤/胜率 |
| `paper.py` | 模拟盘：本地虚拟买卖 + 持仓持久化 + `paper_equity.csv` |
| `report.py` | 每日报告生成（CLI 与网页共用） |
| `holdings.py` | 你的真实持仓管理（网页输入） |
| `app.py` + `templates/` | 本地网页看板 |
| `notify.py` | 推送（Server酱/钉钉），未配置则写本地日志 |
| `runner.py` | 入口：命令行运行 |
| `register_task.ps1` | 注册 Windows 每日定时任务 |

## 网页看板（推荐：每天早上一看便知）

```powershell
cd D:\trading
python app.py
```

浏览器打开 **http://127.0.0.1:5000**，页面包含：

- **市场情绪**（大模型点评 + 热门板块）
- **今日推荐买入 Top 10**（全市场筛选）
- **自选股买卖信号**（买入/卖出/观望 + 置信度）
- **我的持仓实时盈亏**：输入代码/股数/成本价，点「刷新持仓盈亏」实时算总盈亏

> 项目内已带 `vendor` 目录（含 flask），`python app.py` 可直接运行；若你更想装到系统，`pip install flask` 即可。

想让「每天早上醒来就看到 Top 10」：注册定时任务后，它每天 09:00 自动生成 `report.json`，你早上打开网页即是现成结果，无需等待。

## 快速开始

### 1. 安装 Python 3.11+（若未安装）

- 到 https://www.python.org/downloads/ 下载安装，安装时勾选 **Add Python to PATH**。
- 验证：`python --version`

### 2. 安装依赖

```powershell
cd D:\trading
pip install -r requirements.txt
```

### 3. 先跑离线 demo（无需网络、无需 akshare）

```powershell
python runner.py --demo --backtest
```

会生成合成行情，跑一遍策略+回测，输出收益率、回撤、胜率，并打印最新信号。

### 4. 跑真实 A 股行情

```powershell
python runner.py --symbol 600519 --backtest
```

第一次会联网拉数据并缓存到 `data\` 目录。不指定 `--symbol` 则监控 `config.py` 里 `STOCKS` 的全部股票。

> **数据源自动切换**：默认先拉东方财富，失败自动切新浪财经（两者都返回前复权行情），并各重试一次。哪个源能通都能拿到数据。

### 5. 每日策略（新闻/时政 + 全市场选股 + 模拟盘）—— 主功能

```powershell
python runner.py --daily
```

流程：抓 CCTV 时政 + 财联社快讯 + 个股新闻 → 情绪打分（大模型或词典）→ 融合双均线技术信号给出自选股建议 → **全市场选股筛出今天最值得买的 Top N** → 模拟盘自动虚拟买卖 → 推送报告。

- 指定单只：`python runner.py --daily --symbol 600519`
- 跳过全市场选股、只看自选股：`python runner.py --daily --no-screener`
- 只出建议、不动模拟盘：`config.py` 里 `PAPER_TRADING = False`
- 选股数量 / 候选池 / 流动性门槛：`config.py` 里 `SCREEN_TOP_N / SCREEN_CANDIDATE_POOL / SCREEN_MIN_AMOUNT`

## 接大模型做新闻语义分析（可选，强烈推荐）

`config.py` 里填一个 OpenAI 兼容接口的 key 即可启用，默认 DeepSeek：

```python
LLM_API_KEY = "sk-..."                     # platform.deepseek.com 申请
LLM_BASE_URL = "https://api.deepseek.com/v1"
LLM_MODEL = "deepseek-chat"
LLM_PROXY = ""                             # 海外接口需代理时填 http://127.0.0.1:7897
```

填了 key 后：大模型会分析当日新闻给出「市场情绪 + 热门板块 + 个股点评」，并从技术面候选里**精选出最终推荐**（含推荐理由）。没填 key 则自动回退词典法，全流程照常运行。

> 换其它模型：改成对应的 `LLM_BASE_URL` 和 `LLM_MODEL`（如 Moonshot、通义、OpenAI 等任意 OpenAI 兼容接口）。

### 6. 每日自动运行（Windows 任务计划）

在 PowerShell 里执行一次即可注册，之后每天 16:00 收盘后自动跑：

```powershell
.\register_task.ps1
```

手动试跑：`python runner.py --daily`；删除任务：`schtasks /Delete /TN AShareDailyStrategy /F`。

> 循环模式（`--loop --interval 3600`）也能用，但任务计划更稳，且不会占用常驻进程。

## 什么时候卖（卖出规则）

买之前就要定好卖出条件。系统按以下规则自动提醒/模拟卖出（在 `config.py` 可调）：

| 规则 | 默认值 | 说明 |
|------|--------|------|
| 止损 | 亏 8% | `STOP_LOSS_PCT=-0.08`，防止深套 |
| 止盈 | 赚 25% | `TAKE_PROFIT_PCT=0.25`，落袋为安 |
| 移动止损 | 关闭 | `TRAILING_STOP_PCT=0.0`，自最高点回落 X% 卖出 |
| 死叉 | 内建 | 技术面死叉自动给「卖出」建议 |

每日报告的「模拟盘持仓」里会显示每只的**止损价 / 止盈价**，触发时会打印 `⚠️ 触发卖出` 并模拟卖出。

## 配置推送（可选）

在 `config.py` 里填一个即可：

- **Server酱（微信）**：https://sct.ftqq.com 申请 SendKey，填入 `SERVERCHAN_KEY`。
- **钉钉/企业微信机器人**：建群 → 添加机器人 → 复制 Webhook 地址填入 `DINGTALK_WEBHOOK`。

不填则信号只打印到控制台，并写入 `signals.log`。

## 关于系统代理（Clash/V2Ray 用户必看）

akshare 底层用 `requests`，会读取 Windows 系统代理。若你常年挂着 Clash/V2Ray（如 `127.0.0.1:7897`），行情请求可能被代理节点转发导致取数失败（`ProxyError` / `RemoteDisconnected`）。

`config.py` 里默认 `BYPASS_PROXY = True`：程序会**绕过系统代理、直连行情源**，与你的代理互不影响，无需关闭代理。若你在海外、确实需要走代理访问行情源，改为 `False` 并确保代理正常。

## 组合回测（第三阶段）

验证 Top 10 选股是否跑赢基准：

```powershell
python portfolio_backtest.py --universe 150 --top-n 10 --lookback 250 --rebalance 5
# 或：python runner.py --portfolio
```

输出：累计/年化收益、最大回撤、超额收益（对沪深300/中证500）、换手率、Rank IC、Top10 次日/5日/20日收益、行业暴露。

> ⚠️ 当前股票池取「当下流动性 top N」且有幸存者偏差；因子只用技术面（历史新闻不可得）。这是第一版，用于快速证伪策略方向，不等同于实盘收益。

## 如何改成你自己的策略

1. 改 `config.py` 里的 `STOCKS`、`SHORT_MA`、`LONG_MA`、`STOCK_SECTOR`（自选股→板块映射，词典法按它映射时政情绪）、融合权重 `W_TECH/W_STOCK_NEWS/W_MACRO`。
2. 技术策略：在 `strategy.py` 的 `add_indicators()` 里加你的信号规则（如突破、RSI、MACD），保持 `signal` 列输出 `1/-1/0`。
3. 情绪词典：在 `sentiment.py` 的 `POSITIVE_WORDS/NEGATIVE_WORDS/POLICY_RULES` 里增删关键词和板块映射。
4. 选股规则：在 `factors.py` 的 `FACTOR_WEIGHTS`（config）里调多因子权重。
5. 调参看结果：改参数后 `--portfolio` 看组合表现；`--daily` 看当日建议和选股。

## 下一步（进阶路线）

1. 回测通过后再观察一段每日建议，校准信号。
2. 加资金管理（仓位比例、止损、止盈），给全市场选股结果也接模拟盘自动买入。
3. 填 `LLM_API_KEY` 启用大模型，语义分析质量会明显优于词典法。
4. 想真实自动下单：A 股需券商量化终端（QMT/PTrade）或第三方量化平台（聚宽/掘金/米筐），把 `paper.py` 的 `buy/sell` 换成券商下单接口即可，务必先确认券商与监管要求。

## ⚠️ 免责声明

本项目仅供学习研究，不构成任何投资建议。信号与模拟盘结果不代表未来表现；实盘存在滑点、停牌、涨跌停、手续费、监管等风险。词典法情绪分析是启发式方法，会误判。请勿在未充分验证前投入真实资金。
