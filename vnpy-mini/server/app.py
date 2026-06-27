"""
vnpy-mini — 一个套在 vnpy 之上的极简交易/模拟盘界面后端。

设计目标
--------
- 不重复造轮子：交易、行情、撮合全部交给 vnpy 的 MainEngine。
- 这里只做一层很薄的 FastAPI 桥接：把 vnpy 的事件 (tick/order/trade/account/...)
  通过 WebSocket 推给前端；把前端的下单/撤单/订阅请求转成 vnpy 的请求对象。
- 两种运行模式：
    * mock  —— 不需要安装 vnpy，内置一个模拟引擎，随机游走行情 + 本地撮合。
               用来「打开就能看界面」，验证 UI 与数据流。
    * live  —— 真正加载 vnpy + CtpGateway，连 SimNow 做实时模拟盘（或实盘）。

启动:
    WEBMINI_MODE=mock  python -m server.app      # 默认，先看界面
    WEBMINI_MODE=live  python -m server.app      # 需先 pip 安装 vnpy / vnpy_ctp
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MODE = os.getenv("WEBMINI_MODE", "mock").lower()


# --------------------------------------------------------------------------- #
# 请求体模型（前端 -> 后端）
# --------------------------------------------------------------------------- #
class OrderReq(BaseModel):
    symbol: str
    exchange: str = "SHFE"
    direction: str = "LONG"          # LONG / SHORT
    offset: str = "OPEN"             # OPEN / CLOSE
    type: str = "LIMIT"             # LIMIT / MARKET
    price: float = 0.0
    volume: float = 1.0


class CancelReq(BaseModel):
    orderid: str
    symbol: str = ""
    exchange: str = "SHFE"


class SubscribeReq(BaseModel):
    symbol: str
    exchange: str = "SHFE"


class ConnectReq(BaseModel):
    setting: Dict[str, Any] = {}


class StrategyStartReq(BaseModel):
    name: str = "双均线"
    symbol: str
    exchange: str = "SHFE"
    fast: int = 5
    slow: int = 20
    volume: float = 1.0


class StrategyStopReq(BaseModel):
    id: str


# --------------------------------------------------------------------------- #
# 引擎抽象 —— 前端只认这套统一接口，mock 与 live 各实现一份
# --------------------------------------------------------------------------- #
class MAStrategy:
    """示例策略：双均线交叉。快线上穿慢线做多、下穿做空，交叉时反手。

    在引擎内消费实时 tick，自动通过 engine.send_order 发单——mock / live 通用。
    仅作演示，参数与下单逻辑可自行替换。
    """

    def __init__(self, sid, name, symbol, exchange, params, engine):
        self.id = sid
        self.name = name
        self.symbol = symbol
        self.exchange = exchange
        self.fast = max(1, int(params.get("fast", 5)))
        self.slow = max(self.fast + 1, int(params.get("slow", 20)))
        self.volume = float(params.get("volume", 1))
        self.engine = engine
        self.prices: deque = deque(maxlen=self.slow)
        self.signal = 0          # 上一次信号 1/-1/0
        self.net = 0.0           # 本策略累计净头寸（带符号）
        self.trades = 0
        self.active = True

    def on_tick(self, tick: Dict[str, Any]):
        if not self.active:
            return
        self.prices.append(tick["last_price"])
        if len(self.prices) < self.slow:
            return
        prices = list(self.prices)
        fast = sum(prices[-self.fast:]) / self.fast
        slow = sum(prices) / len(prices)
        sig = 1 if fast > slow else (-1 if fast < slow else 0)
        if sig == 0 or sig == self.signal:
            return                # 只在信号翻转时下单
        self.signal = sig
        direction = "LONG" if sig > 0 else "SHORT"
        self.engine.send_order(OrderReq(
            symbol=self.symbol, exchange=self.exchange,
            direction=direction, offset="OPEN", type="LIMIT",
            price=tick["last_price"], volume=self.volume,
        ))
        self.net += self.volume if sig > 0 else -self.volume
        self.trades += 1
        self.engine.on_event("log", {
            "msg": f"[策略 {self.name}] {'多' if sig > 0 else '空'}头信号 → {self.symbol} @ {tick['last_price']}",
            "level": "info",
        })
        self.engine.on_event("strategy", self.engine._strat_info(self))

    def info(self):
        return {
            "id": self.id, "name": self.name, "symbol": self.symbol,
            "exchange": self.exchange, "fast": self.fast, "slow": self.slow,
            "volume": self.volume, "net": self.net, "trades": self.trades,
            "active": self.active,
        }


class EngineBase:
    """统一引擎接口。on_event 由 server 注入，用来把事件推给所有 WebSocket。
    emit() 在转发 tick 给前端的同时，喂给所有在跑的策略。"""

    name = "base"

    def __init__(self, on_event: Callable[[str, Any], None]):
        self.on_event = on_event
        self._strategies: Dict[str, MAStrategy] = {}
        self._strat_seq = 0

    # ---- 事件出口：tick 先喂策略，再推前端 -------------------------------- #
    def emit(self, type_: str, data: Any):
        if type_ == "tick":
            for s in list(self._strategies.values()):
                if s.active and s.symbol == data.get("symbol"):
                    try:
                        s.on_tick(data)
                    except Exception as exc:  # 策略异常不应拖垮行情
                        self.on_event("log", {"msg": f"策略异常: {exc}", "level": "error"})
        self.on_event(type_, data)

    # ---- 策略管理 ---------------------------------------------------------- #
    def start_strategy(self, name, symbol, exchange, params) -> str:
        self._strat_seq += 1
        sid = f"S{self._strat_seq}"
        strat = MAStrategy(sid, name, symbol, exchange, params, self)
        self._strategies[sid] = strat
        self.subscribe(symbol, exchange)            # 确保有行情可消费
        self.on_event("log", {"msg": f"策略启动 {name} {symbol} (MA{strat.fast}/{strat.slow})", "level": "info"})
        self.on_event("strategy", strat.info())
        return sid

    def stop_strategy(self, sid: str):
        s = self._strategies.get(sid)
        if s:
            s.active = False
            self.on_event("log", {"msg": f"策略停止 {s.name} {s.symbol}", "level": "info"})
            self.on_event("strategy", s.info())

    def _strat_info(self, s: MAStrategy):
        return s.info()

    def strategies(self) -> List[Dict[str, Any]]:
        return [s.info() for s in self._strategies.values()]

    # ---- 交易接口（子类实现） --------------------------------------------- #
    def connect(self, setting: Dict[str, Any]) -> None: ...
    def subscribe(self, symbol: str, exchange: str) -> None: ...
    def send_order(self, req: OrderReq) -> str: ...
    def cancel_order(self, req: CancelReq) -> None: ...
    def snapshot(self) -> Dict[str, Any]:
        """REST 首屏快照：账户 / 持仓 / 委托 / 成交 / 策略。"""
        return {"account": {}, "positions": [], "orders": [], "trades": [],
                "ticks": [], "strategies": []}

    def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Mock 引擎：随机游走行情 + 极简本地撮合（限价单价格触及即成交）
# --------------------------------------------------------------------------- #
class MockEngine(EngineBase):
    name = "mock"

    # 一些常见合约的初始价，纯演示用
    _SEED_PRICES = {
        "rb2510": 3300.0,
        "ag2512": 7200.0,
        "IF2509": 3900.0,
        "au2512": 560.0,
        "600000": 10.5,
    }

    def __init__(self, on_event):
        super().__init__(on_event)
        self._lock = threading.Lock()
        self._subscribed: Dict[str, Dict[str, Any]] = {}   # symbol -> last tick state
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._trades: List[Dict[str, Any]] = []
        self._positions: Dict[str, Dict[str, Any]] = {}    # key=symbol.direction
        self._account = {
            "accountid": "MOCK",
            "balance": 1_000_000.0,
            "available": 1_000_000.0,
            "frozen": 0.0,
            "pnl": 0.0,
        }
        self._order_seq = 0
        self._trade_seq = 0
        self._running = True
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()

    # ---- 行情线程 ---------------------------------------------------------- #
    def _tick_loop(self):
        while self._running:
            with self._lock:
                symbols = list(self._subscribed.keys())
            for sym in symbols:
                self._step_tick(sym)
            time.sleep(1.0)

    def _step_tick(self, symbol: str):
        with self._lock:
            st = self._subscribed.get(symbol)
            if st is None:
                return
            last = st["last_price"]
            # 随机游走，步长约 0.05%
            drift = last * 0.0005 * random.uniform(-1.0, 1.0)
            last = round(max(0.01, last + drift), 2)
            spread = max(round(last * 0.0002, 2), 0.01)
            st["last_price"] = last
            st["bid_price_1"] = round(last - spread, 2)
            st["ask_price_1"] = round(last + spread, 2)
            st["volume"] += random.randint(1, 50)
            tick = {
                "symbol": symbol,
                "exchange": st["exchange"],
                "name": symbol,
                "datetime": datetime.now().isoformat(timespec="seconds"),
                "last_price": last,
                "bid_price_1": st["bid_price_1"],
                "ask_price_1": st["ask_price_1"],
                "bid_volume_1": random.randint(1, 100),
                "ask_volume_1": random.randint(1, 100),
                "volume": st["volume"],
            }
            self._match_orders(symbol, last)
        self.emit("tick", tick)          # 推前端 + 喂策略
        self._recalc_pnl()

    # ---- 撮合 -------------------------------------------------------------- #
    def _match_orders(self, symbol: str, price: float):
        """调用方已持锁。限价单：买单价>=现价 或 卖单价<=现价 即全部成交。"""
        for oid, o in list(self._orders.items()):
            if o["symbol"] != symbol or o["status"] in ("全部成交", "已撤销"):
                continue
            hit = (o["direction"] == "LONG" and price <= o["price"]) or (
                o["direction"] == "SHORT" and price >= o["price"]
            )
            if o["type"] == "MARKET":
                hit = True
            if not hit:
                continue
            # 限价单按更有利的一边成交：买取 min(限价,现价)，卖取 max(限价,现价)
            if o["type"] == "MARKET":
                fill_price = price
            elif o["direction"] == "LONG":
                fill_price = min(o["price"], price)
            else:
                fill_price = max(o["price"], price)
            o["traded"] = o["volume"]
            o["status"] = "全部成交"
            self._emit_order(o)
            self._trade_seq += 1
            trade = {
                "tradeid": f"T{self._trade_seq}",
                "orderid": oid,
                "symbol": symbol,
                "exchange": o["exchange"],
                "direction": o["direction"],
                "offset": o["offset"],
                "price": fill_price,
                "volume": o["volume"],
                "datetime": datetime.now().isoformat(timespec="seconds"),
            }
            self._trades.insert(0, trade)
            self._apply_fill(trade)
            self.on_event("trade", trade)

    def _pos_view(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        """内部用带符号净仓 -> 前端用 {direction, volume} 视图。"""
        net = pos["net"]
        return {
            "symbol": pos["symbol"], "exchange": pos["exchange"],
            "direction": "LONG" if net >= 0 else "SHORT",
            "volume": abs(net), "price": pos["price"], "pnl": pos.get("pnl", 0.0),
        }

    def _apply_fill(self, trade: Dict[str, Any]):
        """以带符号净仓维护持仓：开/平/反手都能正确净额。"""
        sym = trade["symbol"]
        vol = trade["volume"]
        delta = vol if trade["direction"] == "LONG" else -vol
        pos = self._positions.get(sym) or {
            "symbol": sym, "exchange": trade["exchange"], "net": 0.0, "price": 0.0, "pnl": 0.0,
        }
        old = pos["net"]
        new = old + delta
        if old == 0 or (old > 0) == (delta > 0):
            # 同向加仓 -> 摊薄均价
            denom = abs(old) + vol
            pos["price"] = round((pos["price"] * abs(old) + trade["price"] * vol) / denom, 2)
        elif new != 0 and (new > 0) != (old > 0):
            # 反手 -> 以成交价为新均价
            pos["price"] = trade["price"]
        pos["net"] = new
        self._positions[sym] = pos

        old_dir = "LONG" if old > 0 else ("SHORT" if old < 0 else None)
        new_dir = "LONG" if new > 0 else ("SHORT" if new < 0 else None)
        # 方向翻转时，先把旧方向清零，前端据此移除旧行
        if old_dir and old_dir != new_dir:
            self.on_event("position", {"symbol": sym, "exchange": pos["exchange"],
                                       "direction": old_dir, "volume": 0, "price": 0, "pnl": 0})
        if new == 0:
            self._positions.pop(sym, None)
            self.on_event("position", {"symbol": sym, "exchange": pos["exchange"],
                                       "direction": new_dir or old_dir, "volume": 0, "price": 0, "pnl": 0})
        else:
            self.on_event("position", self._pos_view(pos))

    def _recalc_pnl(self):
        with self._lock:
            pnl = 0.0
            views = []
            for pos in self._positions.values():
                st = self._subscribed.get(pos["symbol"])
                if not st or not pos["net"]:
                    continue
                # 带符号净仓：盈亏 = (现价 - 均价) * 净仓
                pos["pnl"] = round((st["last_price"] - pos["price"]) * pos["net"], 2)
                pnl += pos["pnl"]
                views.append(self._pos_view(pos))
            self._account["pnl"] = round(pnl, 2)
            self._account["balance"] = round(1_000_000.0 + pnl, 2)
            acct = dict(self._account)
        self.on_event("account", acct)
        for v in views:                 # 让持仓盈亏也实时刷新
            self.on_event("position", v)

    # ---- 对外接口 ---------------------------------------------------------- #
    def connect(self, setting):
        self.on_event("log", {"msg": "Mock 引擎已就绪（无需券商/行情服务器）", "level": "info"})
        self.on_event("account", dict(self._account))

    def subscribe(self, symbol, exchange):
        with self._lock:
            if symbol not in self._subscribed:
                seed = self._SEED_PRICES.get(symbol, round(random.uniform(50, 5000), 2))
                self._subscribed[symbol] = {
                    "exchange": exchange,
                    "last_price": seed,
                    "bid_price_1": seed,
                    "ask_price_1": seed,
                    "volume": 0,
                }
        self.on_event("log", {"msg": f"已订阅 {symbol}.{exchange}", "level": "info"})

    def send_order(self, req: OrderReq) -> str:
        with self._lock:
            self._order_seq += 1
            oid = f"M{self._order_seq}"
            order = {
                "orderid": oid,
                "symbol": req.symbol,
                "exchange": req.exchange,
                "direction": req.direction,
                "offset": req.offset,
                "type": req.type,
                "price": req.price,
                "volume": req.volume,
                "traded": 0.0,
                "status": "未成交",
                "datetime": datetime.now().isoformat(timespec="seconds"),
            }
            self._orders[oid] = order
            # 确保该合约有行情，便于撮合
            if req.symbol not in self._subscribed:
                self.subscribe(req.symbol, req.exchange)
        self._emit_order(order)
        self.on_event("log", {"msg": f"委托提交 {oid} {req.direction} {req.symbol} x{req.volume}@{req.price}", "level": "info"})
        return oid

    def cancel_order(self, req: CancelReq):
        with self._lock:
            o = self._orders.get(req.orderid)
            if o and o["status"] not in ("全部成交", "已撤销"):
                o["status"] = "已撤销"
                self._emit_order(o)

    def _emit_order(self, order):
        self.on_event("order", dict(order))

    def snapshot(self):
        with self._lock:
            ticks = [
                {
                    "symbol": s,
                    "exchange": st["exchange"],
                    "last_price": st["last_price"],
                    "bid_price_1": st["bid_price_1"],
                    "ask_price_1": st["ask_price_1"],
                }
                for s, st in self._subscribed.items()
            ]
            return {
                "account": dict(self._account),
                "positions": [self._pos_view(p) for p in self._positions.values() if p["net"]],
                "orders": list(self._orders.values()),
                "trades": list(self._trades),
                "ticks": ticks,
                "strategies": self.strategies(),
            }

    def close(self):
        self._running = False


# --------------------------------------------------------------------------- #
# Live 引擎：真正的 vnpy MainEngine + CtpGateway
# --------------------------------------------------------------------------- #
class VnpyEngine(EngineBase):
    name = "live"

    def __init__(self, on_event):
        super().__init__(on_event)
        # 延迟导入，未安装 vnpy 时不影响 mock 模式
        from vnpy.event import EventEngine
        from vnpy.trader.engine import MainEngine
        from vnpy.trader.event import (
            EVENT_TICK, EVENT_ORDER, EVENT_TRADE,
            EVENT_POSITION, EVENT_ACCOUNT, EVENT_LOG,
        )
        from vnpy_ctp import CtpGateway

        self._EventEngine = EventEngine
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.gateway_name = "CTP"
        self.main_engine.add_gateway(CtpGateway)

        ee = self.event_engine
        ee.register(EVENT_TICK, lambda e: self.emit("tick", self._tick(e.data)))
        ee.register(EVENT_ORDER, lambda e: self.on_event("order", self._order(e.data)))
        ee.register(EVENT_TRADE, lambda e: self.on_event("trade", self._trade(e.data)))
        ee.register(EVENT_POSITION, lambda e: self.on_event("position", self._position(e.data)))
        ee.register(EVENT_ACCOUNT, lambda e: self.on_event("account", self._account(e.data)))
        ee.register(EVENT_LOG, lambda e: self.on_event("log", {"msg": getattr(e.data, "msg", str(e.data)), "level": "info"}))

    # ---- vnpy 对象 -> dict ------------------------------------------------- #
    @staticmethod
    def _enum(v):
        return getattr(v, "value", v)

    def _tick(self, t):
        return {
            "symbol": t.symbol, "exchange": self._enum(t.exchange), "name": t.name,
            "datetime": t.datetime.isoformat(timespec="seconds") if t.datetime else "",
            "last_price": t.last_price, "volume": t.volume,
            "bid_price_1": t.bid_price_1, "ask_price_1": t.ask_price_1,
            "bid_volume_1": t.bid_volume_1, "ask_volume_1": t.ask_volume_1,
        }

    def _order(self, o):
        return {
            "orderid": o.orderid, "symbol": o.symbol, "exchange": self._enum(o.exchange),
            "direction": self._enum(o.direction), "offset": self._enum(o.offset),
            "type": self._enum(o.type), "price": o.price, "volume": o.volume,
            "traded": o.traded, "status": self._enum(o.status),
            "datetime": o.datetime.isoformat(timespec="seconds") if o.datetime else "",
        }

    def _trade(self, t):
        return {
            "tradeid": t.tradeid, "orderid": t.orderid, "symbol": t.symbol,
            "exchange": self._enum(t.exchange), "direction": self._enum(t.direction),
            "offset": self._enum(t.offset), "price": t.price, "volume": t.volume,
            "datetime": t.datetime.isoformat(timespec="seconds") if t.datetime else "",
        }

    def _position(self, p):
        return {
            "symbol": p.symbol, "exchange": self._enum(p.exchange),
            "direction": self._enum(p.direction), "volume": p.volume,
            "price": p.price, "pnl": p.pnl,
        }

    def _account(self, a):
        return {
            "accountid": a.accountid, "balance": a.balance,
            "available": a.available, "frozen": a.frozen,
            "pnl": getattr(a, "pnl", 0.0),
        }

    # ---- dict -> vnpy 请求对象 -------------------------------------------- #
    def connect(self, setting):
        # setting 形如 SimNow 的连接字典，键为中文（见 README）
        self.main_engine.connect(setting, self.gateway_name)
        self.on_event("log", {"msg": "已发起 CTP 连接（SimNow）…", "level": "info"})

    def subscribe(self, symbol, exchange):
        from vnpy.trader.object import SubscribeRequest
        from vnpy.trader.constant import Exchange
        req = SubscribeRequest(symbol=symbol, exchange=Exchange(exchange))
        self.main_engine.subscribe(req, self.gateway_name)

    def send_order(self, req: OrderReq) -> str:
        from vnpy.trader.object import OrderRequest
        from vnpy.trader.constant import Direction, Offset, OrderType, Exchange
        vreq = OrderRequest(
            symbol=req.symbol,
            exchange=Exchange(req.exchange),
            direction=Direction.LONG if req.direction == "LONG" else Direction.SHORT,
            type=OrderType.MARKET if req.type == "MARKET" else OrderType.LIMIT,
            volume=req.volume,
            price=req.price,
            offset=Offset.OPEN if req.offset == "OPEN" else Offset.CLOSE,
            reference="vnpy-mini",
        )
        return self.main_engine.send_order(vreq, self.gateway_name)

    def cancel_order(self, req: CancelReq):
        from vnpy.trader.object import CancelRequest
        from vnpy.trader.constant import Exchange
        vreq = CancelRequest(
            orderid=req.orderid, symbol=req.symbol, exchange=Exchange(req.exchange)
        )
        self.main_engine.cancel_order(vreq, self.gateway_name)

    def snapshot(self):
        me = self.main_engine
        accounts = me.get_all_accounts()
        return {
            "account": self._account(accounts[0]) if accounts else {},
            "positions": [self._position(p) for p in me.get_all_positions()],
            "orders": [self._order(o) for o in me.get_all_orders()],
            "trades": [self._trade(t) for t in me.get_all_trades()],
            "ticks": [self._tick(t) for t in me.get_all_ticks()],
            "strategies": self.strategies(),
        }

    def close(self):
        self.main_engine.close()


# --------------------------------------------------------------------------- #
# WebSocket 广播管理
# --------------------------------------------------------------------------- #
class WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def _broadcast(self, message: str):
        for ws in list(self.active):
            try:
                await ws.send_text(message)
            except Exception:
                self.disconnect(ws)

    def push(self, type_: str, data: Any):
        """线程安全：可从 vnpy / mock 的后台线程调用。"""
        if self.loop is None:
            return
        msg = json.dumps({"type": type_, "data": data}, ensure_ascii=False)
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self.loop)


manager = WSManager()
engine: Optional[EngineBase] = None

app = FastAPI(title="vnpy-mini")


@app.on_event("startup")
async def _startup():
    global engine
    manager.loop = asyncio.get_event_loop()

    def on_event(type_: str, data: Any):
        manager.push(type_, data)

    if MODE == "live":
        try:
            engine = VnpyEngine(on_event)
        except Exception as exc:  # 未装 vnpy / vnpy_ctp
            raise RuntimeError(
                f"live 模式需要安装 vnpy 与 vnpy_ctp：{exc}"
            ) from exc
    else:
        engine = MockEngine(on_event)


@app.on_event("shutdown")
async def _shutdown():
    if engine:
        engine.close()


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #
@app.get("/api/status")
async def status():
    return {"mode": engine.name if engine else "none", "ok": True}


@app.get("/api/snapshot")
async def snapshot():
    return engine.snapshot()


@app.post("/api/connect")
async def connect(req: ConnectReq):
    engine.connect(req.setting)
    return {"ok": True}


@app.post("/api/subscribe")
async def subscribe(req: SubscribeReq):
    engine.subscribe(req.symbol, req.exchange)
    return {"ok": True}


@app.post("/api/order")
async def order(req: OrderReq):
    oid = engine.send_order(req)
    return {"ok": True, "orderid": oid}


@app.post("/api/cancel")
async def cancel(req: CancelReq):
    engine.cancel_order(req)
    return {"ok": True}


@app.get("/api/strategies")
async def list_strategies():
    return engine.strategies()


@app.post("/api/strategy/start")
async def strategy_start(req: StrategyStartReq):
    sid = engine.start_strategy(
        req.name, req.symbol, req.exchange,
        {"fast": req.fast, "slow": req.slow, "volume": req.volume},
    )
    return {"ok": True, "id": sid}


@app.post("/api/strategy/stop")
async def strategy_stop(req: StrategyStopReq):
    engine.stop_strategy(req.id)
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # 连接后先推一份快照
        await ws.send_text(json.dumps({"type": "snapshot", "data": engine.snapshot()}, ensure_ascii=False))
        while True:
            await ws.receive_text()  # 前端目前只接收，保持连接
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


# 静态前端（放在最后挂载，避免覆盖上面的 /api 与 /ws 路由）
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("WEBMINI_HOST", "127.0.0.1")
    port = int(os.getenv("WEBMINI_PORT", "8000"))
    print(f"\n  vnpy-mini  [{MODE} 模式]  ->  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port)
