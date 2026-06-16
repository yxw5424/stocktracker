"""推送渠道:仅当对应环境变量存在时启用;无凭证则静默跳过(网站照常更新)。"""
from __future__ import annotations

import os
import smtplib
from email.mime.text import MIMEText

import requests


def _pushplus(title, content):
    token = os.getenv("PUSHPLUS_TOKEN")
    if not token:
        return False
    requests.post("https://www.pushplus.plus/send",
                  json={"token": token, "title": title, "content": content, "template": "txt"},
                  timeout=15)
    return True


def _serverchan(title, content):
    key = os.getenv("SERVERCHAN_KEY")
    if not key:
        return False
    requests.post(f"https://sctapi.ftqq.com/{key}.send",
                  data={"title": title, "desp": content}, timeout=15)
    return True


def _bark(title, content):
    url = os.getenv("BARK_URL")
    if not url:
        return False
    requests.get(f"{url.rstrip('/')}/{requests.utils.quote(title)}/{requests.utils.quote(content)}", timeout=15)
    return True


def _telegram(title, content):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return False
    requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": f"{title}\n{content}"}, timeout=15)
    return True


def _email(title, content):
    host = os.getenv("SMTP_HOST")
    if not host:
        return False
    port = int(os.getenv("SMTP_PORT", "465"))
    user, pwd = os.getenv("SMTP_USER"), os.getenv("SMTP_PASS")
    to = os.getenv("SMTP_TO", user)
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = title, user, to
    with smtplib.SMTP_SSL(host, port, timeout=20) as s:
        s.login(user, pwd)
        s.sendmail(user, [to], msg.as_string())
    return True


_CHANNELS = [
    ("pushplus", _pushplus), ("serverchan", _serverchan), ("bark", _bark),
    ("telegram", _telegram), ("email", _email),
]


def send(title: str, content: str) -> list[str]:
    """返回成功发送的渠道名列表。"""
    sent = []
    for name, fn in _CHANNELS:
        try:
            if fn(title, content):
                sent.append(name)
        except Exception as e:  # 单个渠道失败不影响其它
            print(f"[notify] {name} failed: {e}")
    return sent
