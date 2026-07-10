# -*- coding: utf-8 -*-
"""
华泰证券模拟盘 REST 客户端(自研薄封装,不依赖官方 skill 包框架)。

接口契约来自官方 a-share-paper-trading skill 的源码:
  base = HTSC_API_URL(默认 https://ai.zhangle.com) + /edge/entry/gate
  headers = {apiKey: HT_APIKEY, skillCode: mx_1778741794549, Content-Type: json}
  端点(POST):/api/simSkills/{searchStock,getQuote,getAccountBalance,
              getPositions,submitOrder,cancelOrder,listPendingOrders,listTradeHistory}

用途:让我们的 AI 复核官把"纸面决定"真发到华泰模拟盘参赛(ETF 巅峰赛)。
安全:默认 dry-run(只返回将要下的单,不真发);置 HTSC_LIVE=1 才真提交。
"""
import os
import json
import urllib.request
import urllib.error

_SKILL_CODE = "mx_1778741794549"


class HTSCBroker:
    def __init__(self, api_key=None, api_url=None, live=None, timeout=20):
        self.api_key = api_key or os.getenv("HT_APIKEY", "")
        self.api_url = (api_url or os.getenv("HTSC_API_URL", "https://ai.zhangle.com")).rstrip("/")
        self.base = os.getenv("HTSC_BASE_URL", "/edge/entry/gate")
        self.live = (os.getenv("HTSC_LIVE", "0") == "1") if live is None else live
        self.timeout = timeout

    def _post(self, path, body=None):
        if not self.api_key:
            return {"ok": False, "error": {"message": "缺少 HT_APIKEY"}}
        url = self.api_url + self.base + path
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "apiKey": self.api_key, "skillCode": _SKILL_CODE,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": {"message": f"HTTP {e.code}", "body": e.read().decode("utf-8", "ignore")[:300]}}
        except Exception as e:
            return {"ok": False, "error": {"message": f"{type(e).__name__}: {e}"}}

    # --- 只读接口(dry-run 也会真查,方便看账户状态) ---
    def search_stock(self, query, limit=10):
        return self._post("/api/simSkills/searchStock", {"query": query, "limit": limit})

    def get_quote(self, stock_code, exchange):
        return self._post("/api/simSkills/getQuote", {"stockCode": stock_code, "exchange": exchange})

    def account_balance(self):
        return self._post("/api/simSkills/getAccountBalance")

    def positions(self):
        return self._post("/api/simSkills/getPositions")

    def pending_orders(self):
        return self._post("/api/simSkills/listPendingOrders", {})

    def trade_history(self, start=None, end=None):
        """成交记录;后端要求 YYYY-MM-DD 起止日期,默认查最近 7 天。"""
        import datetime as _dt
        end = end or _dt.date.today().strftime("%Y-%m-%d")
        start = start or (_dt.date.today() - _dt.timedelta(days=7)).strftime("%Y-%m-%d")
        return self._post("/api/simSkills/listTradeHistory",
                          {"startDate": start, "endDate": end})

    # --- 写接口:受 live 开关保护 ---
    def submit_order(self, direction, stock_code, exchange, quantity,
                     order_type="limit", price=None):
        """direction: buy/sell;exchange: SH/SZ/BJ;quantity: 股数;
        order_type: limit(限价,需 price)/market(市价)。"""
        body = {"direction": direction, "stockCode": stock_code, "exchange": exchange,
                "quantity": int(quantity), "orderType": order_type}
        if price is not None:
            body["price"] = float(price)
        if not self.live:
            return {"ok": True, "dry_run": True, "would_submit": body,
                    "note": "HTSC_LIVE!=1,未真下单(干跑)"}
        return self._post("/api/simSkills/submitOrder", body)

    def cancel_order(self, order_id):
        if not self.live:
            return {"ok": True, "dry_run": True, "would_cancel": order_id}
        return self._post("/api/simSkills/cancelOrder", {"orderId": order_id})


def split_symbol(sym):
    """'510300.SH' -> ('510300','SH')。已是6位纯代码则按首位猜交易所。"""
    if "." in sym:
        code, ex = sym.split(".", 1)
        return code, ex.upper()
    code = sym
    # 上交所:6xxxxx股票 / 5xxxxx/56/58 ETF / 9xx B股;深交所:0/3 股票 / 1(15/16/18) ETF;北交所 4/8/920/430
    if code[0] in "56" or code[0] == "9":
        ex = "SH"
    elif code[0] in "48" or code[:3] in ("920", "430"):
        ex = "BJ"
    else:                      # 0/1/3 开头 → 深交所(含 15/16/18 深市ETF)
        ex = "SZ"
    return code, ex


if __name__ == "__main__":
    # 连通性自检:python htsc_broker.py  → 查账户(只读,不下单)
    b = HTSCBroker()
    print("live =", b.live)
    print("account:", json.dumps(b.account_balance(), ensure_ascii=False)[:500])
    print("positions:", json.dumps(b.positions(), ensure_ascii=False)[:500])
