"""本地常驻运行:真正的"动态间隔"——斜率激增就缩短 sleep。

    python scripts/run_local.py          # 联网,仅交易时段取数
    python scripts/run_local.py --demo   # 离线合成数据,演示自适应节奏

Ctrl+C 退出。网站可同时用 `python -m http.server -d docs 8000` 本地预览。
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


def next_sleep_seconds(default: int = 3600) -> int:
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
        return max(60, int(d.get("next_interval_minutes", 60)) * 60)
    except Exception:
        return default


def main() -> None:
    demo = "--demo" in sys.argv
    print("本地监控启动(Ctrl+C 退出)")
    try:
        while True:
            run_once(demo)
            sl = next_sleep_seconds()
            print(f"下次 {sl // 60} 分钟后再跑…")
            time.sleep(sl)
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
