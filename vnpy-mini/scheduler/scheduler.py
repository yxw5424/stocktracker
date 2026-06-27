"""
交易日调度器 —— 在 A股开盘前与收盘后触发 AI 顾问例程。

无第三方依赖，基于 asyncio：算出下一个触发时刻 → 睡到那一刻 → 触发 → 循环。
默认时区 Asia/Shanghai，触发点：
  * 09:15  开盘前（决定今日是否操作）   -> session="open"
  * 15:05  收盘后（复盘并规划下一日）   -> session="close"
仅跳过周末；法定节假日未内置（可接交易日历完善）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Asia/Shanghai")
except Exception:                       # 极少数环境无 tz 数据
    _TZ = None

OPEN_T = time(9, 15)
CLOSE_T = time(15, 5)


def _is_trading_day(d: datetime) -> bool:
    return d.weekday() < 5            # 0-4 = 周一~周五（不含节假日）


def next_fire(now: datetime) -> Tuple[datetime, str]:
    """给定当前时间，返回下一个 (触发时刻, session) 。纯函数，便于测试。"""
    candidates: List[Tuple[datetime, str]] = []
    for day_offset in range(0, 5):
        day = (now + timedelta(days=day_offset)).date()
        for t, sess in ((OPEN_T, "open"), (CLOSE_T, "close")):
            dt = datetime.combine(day, t, tzinfo=now.tzinfo)
            if dt > now and _is_trading_day(dt):
                candidates.append((dt, sess))
    candidates.sort(key=lambda x: x[0])
    return candidates[0]


class MarketScheduler:
    def __init__(self, on_event: Callable[[str], Awaitable[None]]):
        self.on_event = on_event
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.last_fired: Optional[Tuple[str, str]] = None   # (iso 时间, session)

    def _now(self) -> datetime:
        return datetime.now(_TZ) if _TZ else datetime.now()

    async def _loop(self):
        while self._running:
            fire_at, session = next_fire(self._now())
            delay = (fire_at - self._now()).total_seconds()
            try:
                await asyncio.sleep(max(delay, 1))
            except asyncio.CancelledError:
                break
            if not self._running:
                break
            self.last_fired = (self._now().isoformat(timespec="seconds"), session)
            try:
                await self.on_event(session)
            except Exception:
                pass                     # 单次失败不应中断调度

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.ensure_future(self._loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def trigger(self, session: str = "open"):
        """手动触发一次（开盘/收盘例程），用于测试或即时复盘。"""
        await self.on_event(session)

    def upcoming(self, n: int = 4) -> List[dict]:
        """未来 n 个触发点，给前端展示。"""
        out, cur = [], self._now()
        for _ in range(n):
            dt, sess = next_fire(cur)
            out.append({"at": dt.isoformat(timespec="minutes"), "session": sess})
            cur = dt
        return out
