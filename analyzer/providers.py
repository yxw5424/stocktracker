"""数据 Provider 抽象:上层引擎只依赖这个接口,不关心数据从哪来。

当前用新浪/akshare 实现(SinaProvider,免费,够开发与自用)。
**将来你的付费/券商量化 API 只要实现同样这几个方法,就能无缝替换**——
上层多维信号引擎一行都不用改。这是"可拓展性最高"的落点。

    # 将来:
    class QuantProvider(DataProvider):
        name = "quant"
        def spot_all(self): ...      # 接你的实时全市场接口
        def indices(self): ...
        def minute_1(self, code): ...
        def daily(self, code, days=120): ...
    # 然后 get_provider("quant") 即可,其余代码不动。
"""
from __future__ import annotations

import pandas as pd

from . import fetch as fetchmod
from . import screen as screenmod


class DataProvider:
    """数据源接口。新增数据源 = 继承并实现这 4 个方法。"""
    name = "base"

    def spot_all(self) -> pd.DataFrame:
        """全市场实时快照:code/name/price/pct/open/prev_close/high/low/volume/amount。"""
        raise NotImplementedError

    def indices(self) -> pd.DataFrame:
        """大盘指数快照:code/name/price/pct/prev_close/...。"""
        raise NotImplementedError

    def minute_1(self, code: str) -> pd.DataFrame:
        raise NotImplementedError

    def daily(self, code: str, days: int = 120) -> pd.DataFrame:
        raise NotImplementedError

    def sectors(self) -> pd.DataFrame:
        """行业板块快照:sector/count/pct/amount。"""
        raise NotImplementedError


_IDX_REN = {"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "pct",
            "昨收": "prev_close", "今开": "open", "最高": "high", "最低": "low",
            "成交量": "volume", "成交额": "amount"}


class SinaProvider(DataProvider):
    """新浪/akshare 实现(免费)。全市场快照 / 指数各 1 个请求,符合防封。"""
    name = "sina"

    def spot_all(self) -> pd.DataFrame:
        return screenmod.fetch_spot_all()

    def indices(self) -> pd.DataFrame:
        import akshare as ak

        fetchmod._polite_pause()
        df = fetchmod._retry(lambda: ak.stock_zh_index_spot_sina())
        df = df.rename(columns=_IDX_REN)
        for c in ["price", "pct", "prev_close", "open", "high", "low", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def minute_1(self, code: str) -> pd.DataFrame:
        return fetchmod.fetch_1min(code)

    def daily(self, code: str, days: int = 120) -> pd.DataFrame:
        return fetchmod.fetch_daily(code, days)

    def sectors(self) -> pd.DataFrame:
        import akshare as ak

        fetchmod._polite_pause()
        df = fetchmod._retry(lambda: ak.stock_sector_spot(indicator="新浪行业"))
        df = df.rename(columns={"板块": "sector", "公司家数": "count",
                                "涨跌幅": "pct", "总成交额": "amount"})
        for c in ["pct", "amount", "count"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        keep = [c for c in ["sector", "count", "pct", "amount"] if c in df.columns]
        return df[keep].dropna(subset=["pct"])


_PROVIDERS = {"sina": SinaProvider}


def get_provider(name: str = "sina") -> DataProvider:
    """工厂:按名取 provider。将来注册 'quant' 等到 _PROVIDERS 即可。"""
    return _PROVIDERS.get(name, SinaProvider)()
