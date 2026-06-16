"""本地常驻运行:真正的"动态间隔"——斜率激增就缩短 sleep——并自动把数据推到 GitHub。

为什么要本地跑:akshare 抓的是国内财经站点,GitHub 的海外服务器抓不到;在你本地
(国内网络)跑最稳,跑完把 docs/data 推到 GitHub,网站(Pages)就自动更新。

    python scripts/run_local.py            # 联网抓数 + 自动 push,仅交易时段取数
    python scripts/run_local.py --demo     # 离线合成数据(演示自适应节奏)
    python scripts/run_local.py --no-push   # 只在本地跑,不推送

Ctrl+C 退出。本地预览网站:python -m http.server -d docs 8010
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "docs", "data", "data.json")


def run_once(demo: bool) -> None:
    cmd = [sys.executable, "-m", "analyzer.run"] + (["--demo"] if demo else [])
    subprocess.run(cmd, cwd=ROOT, check=False)


def git_push() -> None:
    """把最新数据提交并推到 GitHub。用系统默认网络(含代理),以便正常访问 github。"""
    subprocess.run(["git", "add", "docs/data"], cwd=ROOT, check=False)
    # 没有变化就不提交,避免空 commit
    if subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=ROOT).returncode == 0:
        return
    subprocess.run(["git", "commit", "-q", "-m", "data: local update"], cwd=ROOT, check=False)
    ok = subprocess.run(["git", "push", "-q"], cwd=ROOT, check=False).returncode == 0
    print("[push] 已推送数据到 GitHub,网站稍后自动更新" if ok
          else "[push] 推送失败,检查网络/代理/git 凭据后可手动 git push")


def next_sleep_seconds(default: int = 3600) -> int:
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
        return max(60, int(d.get("next_interval_minutes", 60)) * 60)
    except Exception:
        return default


def main() -> None:
    demo = "--demo" in sys.argv
    push = "--no-push" not in sys.argv
    print(f"本地监控启动({'演示' if demo else '联网'} 模式,{'自动推送' if push else '不推送'};Ctrl+C 退出)")
    try:
        while True:
            run_once(demo)
            if push:
                git_push()
            sl = next_sleep_seconds()
            print(f"下次 {sl // 60} 分钟后再跑…")
            time.sleep(sl)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
