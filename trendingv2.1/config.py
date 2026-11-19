# -*- coding: utf-8 -*-
"""
trendingv2.1 全局配置：标的、策略参数、回测参数、推送配置。

首次使用：先只改 STOCKS 和 MA 参数，推送等跑通信号后再配。
本项目仅 paper + 真实持仓 advisory，不接券商实盘下单。
"""
from pathlib import Path
import os

# ---------- 项目 ----------
PROJECT_NAME = "trendingv2.1"
PROJECT_TITLE = "trendingv2.1 A股辅助看板"

# ---------- 数据 ----------
# 要监控的 A 股（代码 -> 名称）。代码为 6 位，如 600519=贵州茅台，000001=平安银行。
STOCKS = {
    "600519": "贵州茅台",
    "000001": "平安银行",
    "601318": "中国平安",
}

# 行情周期：daily / weekly / monthly
PERIOD = "daily"
# 复权方式：qfq=前复权（推荐，适合技术指标），hfq=后复权，空=不复权
ADJUST = "qfq"
# 拉取历史数据起始日期（回测需要足够长，默认 3 年）
START_DATE = "20220101"

# 本地数据缓存目录（避免重复请求行情源）
DATA_DIR = Path(__file__).parent / "data"

# 网络代理：akshare 底层 requests 会读取系统代理（如 Clash/V2Ray 的 127.0.0.1:7897）。
# 代理没开/抽风时会报 ProxyError。国内访问 eastmoney 行情源通常直连即可，默认绕过系统代理。
# 若你在海外、确实需要走代理，改为 False 并确保代理已启动。
BYPASS_PROXY = True

# 可选行情适配：设置 FUYAO_API_KEY 时 data.fetch_fuyao 可用；未设置则跳过，沿用东财/新浪。
FUYAO_API_KEY = os.environ.get("FUYAO_API_KEY", "")
FUYAO_BASE_URL = os.environ.get("FUYAO_BASE_URL", "")

# ---------- 策略（双均线金叉/死叉） ----------
SHORT_MA = 5   # 短期均线窗口
LONG_MA = 20   # 长期均线窗口
# 金叉（短线上穿长线）买入，死叉（短线下穿长线）卖出

# ---------- 回测 ----------
INITIAL_CAPITAL = 1_000_000.0  # 初始资金（A股按手交易，高价股如茅台需足够本金）
COMMISSION_RATE = 0.0003      # 佣金（万3，双边，最低5元）
STAMP_TAX_RATE = 0.0005       # 印花税（卖出单边，现行 0.05%）
SLIPPAGE = 0.001              # 滑点（0.1%，模拟成交价偏移）

# ---------- 推送（跑通信号后再填） ----------
# 方式一：Server酱（微信推送），https://sct.ftqq.com 免费申请，填 SendKey
SERVERCHAN_KEY = ""
# 方式二：钉钉/企业微信机器人 Webhook 完整地址
DINGTALK_WEBHOOK = ""
# 留空则只打印到控制台并写入本地日志（默认，安全）

# ---------- 消息面 / 信号融合 ----------
# 股票所属板块（用于把时政/宏观新闻映射到个股）。按代码或名称匹配均可。
STOCK_SECTOR = {
    "600519": "消费",   # 白酒
    "000001": "银行",
    "601318": "金融",
    "600664": "医药",   # 哈药股份
    "603259": "医药",   # 药明康德
}

# 信号融合权重：技术面 + 个股消息 + 板块时政情绪
W_TECH = 0.5        # 技术面（双均线信号，取值为 -1/0/1）
W_STOCK_NEWS = 0.3  # 个股新闻情绪 [-1,1]
W_MACRO = 0.2       # 板块时政情绪 [-1,1]
# 综合得分阈值（combined = W_TECH*tech + W_STOCK_NEWS*stock + W_MACRO*sector）
BUY_THRESHOLD = 0.25              # 高于此值 -> 买入
SELL_THRESHOLD = -0.25            # 低于此值 -> 卖出（无持仓/通用）
# 持仓时的宽松卖出门槛：有纸面或真实仓位时，combined <= 此值即倾向卖出（缓解买易卖难）
HOLDING_SELL_THRESHOLD = -0.12

# ---------- 模拟盘（自动买卖演练，安全，无需券商） ----------
PAPER_TRADING = True   # True=按建议自动模拟买卖；False=只出建议不动模拟盘
POSITION_PCT = 0.10    # 单只目标仓位（等权，占账户权益比例）

# CLI 与 Web 日报是否默认执行模拟盘买卖 / 风控出场（可分别覆盖）
# Web 原先写死 trade=False，导致网页重建日报不跑止损；现默认与 CLI 对齐。
REPORT_TRADE_DEFAULT = True
WEB_REPORT_TRADE = True   # app /api/report 使用；设 False 可恢复「只看不动模拟盘」

# ---------- 卖出规则（风控：买之前先定好什么时候卖） ----------
STOP_LOSS_PCT = -0.08      # 止损：亏 8% 卖出
TAKE_PROFIT_PCT = 0.25     # 止盈：赚 25% 卖出
TRAILING_STOP_PCT = 0.0    # 移动止损：自最高点回落 X% 卖出（0=关闭）
# 止损/止盈命中时动作固定为「必须卖出」，覆盖综合分阈值；
# 模拟盘自动卖出；真实持仓仅 advisory（写入日报 risk_notes / action），不接券商。

# ---------- 大模型语义分析（可选，填了 key 才启用） ----------
# 推荐用环境变量，避免密钥写进代码被误提交： export LLM_API_KEY=sk-...
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_PROXY = ""                              # 访问海外接口需代理时填，如 http://127.0.0.1:7897

# ---------- 全市场选股（每天推荐买入的股票） ----------
SCREEN_ENABLED = True       # 是否在 --daily 里跑全市场选股
SCREEN_MIN_AMOUNT = 5e8     # 最低成交额(元)，过滤流动性差的股票
SCREEN_UNIVERSE_SIZE = 300  # 因子评分股票池大小（按成交额取前 N 只）
SCREEN_TOP_N = 10           # 每天推荐买入的股票数量
SCREEN_WORKERS = 10         # 全市场选股并发拉历史行情的线程数（提速；过大易被行情源限频）
# 固定风险门控（不做动态调参）：风险状态下允许的最多推荐数，以及绝对入选门槛。
SCREEN_RISK_MAX_PICKS = 3
SCREEN_NEUTRAL_MAX_PICKS = 6
SCREEN_MIN_FINAL_SCORE = 0.58
SCREEN_MAX_RET5_PCT = 20.0  # 短期涨幅超过该值视为过热，不再追入

# 多因子权重（第二阶段，合计 1.0）
FACTOR_WEIGHTS = {
    "trend": 0.25,      # 趋势：价格 vs MA20 / MA20 vs MA60
    "rs": 0.20,         # 相对强度：20日收益 - 沪深300
    "mid_mom": 0.15,    # 中期动量：20日收益
    "short_mom": 0.10,  # 短期动量：5日收益
    "vol": 0.10,        # 波动控制（高扣分）
    "dd": 0.10,         # 回撤控制（深扣分）
    "liq": 0.05,        # 流动性：20日均成交额
    "news": 0.05,       # 新闻情绪：LLM 结构化评分
}
