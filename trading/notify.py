# -*- coding: utf-8 -*-
"""
推送层：把信号发到手机。未配置任何通道时，只打印控制台并写入本地日志（默认，安全）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import config

LOG_FILE = Path(__file__).parent / "signals.log"

logger = logging.getLogger("notify")
if not logger.handlers:
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _serverchan(title: str, text: str) -> None:
    import requests  # 惰性导入：未配置推送时无需安装
    url = f"https://sctapi.ftqq.com/{config.SERVERCHAN_KEY}.send"
    requests.post(url, data={"title": title, "desp": text}, timeout=10)


def _dingtalk(title: str, text: str) -> None:
    import requests  # 惰性导入：未配置推送时无需安装
    requests.post(
        config.DINGTALK_WEBHOOK,
        json={"msgtype": "text", "text": {"content": f"{title}\n{text}"}},
        timeout=10,
    )


def send(title: str, text: str) -> None:
    """发送信号；所有通道都失败时退回控制台与本地日志。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] {title}\n{text}\n")

    sent = False
    if config.SERVERCHAN_KEY:
        try:
            _serverchan(title, text)
            sent = True
        except Exception as exc:  # pragma: no cover
            logger.warning("Server酱推送失败: %s", exc)
    if config.DINGTALK_WEBHOOK:
        try:
            _dingtalk(title, text)
            sent = True
        except Exception as exc:  # pragma: no cover
            logger.warning("钉钉推送失败: %s", exc)

    logger.info("%s | %s", title, text)
    if not sent:
        logger.info("(未配置推送通道，仅本地记录)")
