"""可扩展信号框架 + 信息筛选。

每个"维度"就是一个 collector 函数:(ctx) -> list[Signal]。
**加新维度只需写一个函数并 @register**(如板块轮动、北向资金、新闻情绪、龙虎榜…),
fuse() 自动把所有维度的信号汇总、去重、按重要性排序截断 —— 这就是"信息筛选"。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Signal:
    dim: str                 # 维度:market / stock / watchlist / sector / news ...
    kind: str                # 信号子类型
    subject: str             # 'MARKET' 或 代码 或 板块名
    level: str               # info / notice / high
    score: float             # 重要性,用于筛选排序
    message: str
    data: dict = field(default_factory=dict)


_COLLECTORS = []


def register(fn):
    """注册一个维度采集器。"""
    _COLLECTORS.append(fn)
    return fn


def collect_all(ctx) -> list[Signal]:
    out: list[Signal] = []
    for fn in _COLLECTORS:
        try:
            out.extend(fn(ctx) or [])
        except Exception as e:
            print(f"[signal] {getattr(fn, '__name__', '?')} failed: {e}")
    return out


def fuse(ctx, per_dim: int = 8, total: int = 25) -> list[dict]:
    """信息筛选:每维度先按 score 取前 per_dim,再全局按 score 排序截断到 total。"""
    sigs = collect_all(ctx)
    by_dim: dict[str, list[Signal]] = {}
    for s in sorted(sigs, key=lambda x: x.score, reverse=True):
        lst = by_dim.setdefault(s.dim, [])
        if len(lst) < per_dim:
            lst.append(s)
    kept = [s for lst in by_dim.values() for s in lst]
    kept.sort(key=lambda x: x.score, reverse=True)
    return [asdict(s) for s in kept[:total]]
