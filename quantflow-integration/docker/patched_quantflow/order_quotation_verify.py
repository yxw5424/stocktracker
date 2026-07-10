from panda_backtest.backtest_common.constant.strategy_constant import SIDE_BUY, CLOSE, REJECTED
import logging

from panda_backtest.backtest_common.data.quotation.quotation_data import QuotationData

from panda_backtest.backtest_common.system.context.core_context import CoreContext
from panda_backtest.util.log.remote_log_factory import RemoteLogFactory
class OrderQuotationVerify(object):
    def __init__(self):
        self.context = CoreContext.get_instance()

    def get_order_market_price(self, order_result):
        bar_dict = QuotationData.get_instance().bar_dict
        sr_logger = RemoteLogFactory.get_sr_logger()
        strategy_context = self.context.strategy_context
        run_info = strategy_context.run_info
        # [patched] 下单日该标的无行情(停牌/数据缺口)时 bar 为 None,原代码直接 .open 崩溃。
        # 返回 0 让调用方按 SYMBOL_NO_QUOTATION 干净拒单,回测继续,不再中断整段。
        bar = bar_dict[order_result.order_book_id]
        if bar is None:
            try:
                sr_logger.error("下单日无行情(停牌/数据缺口),拒单：%s" % order_result.order_book_id)
            except Exception:
                pass
            return 0
        if run_info.matching_type == 1:
            hq_price = bar.open
        elif run_info.matching_type == 3:
            hq_price = bar.last
        else:
            hq_price = bar.close
        return hq_price if hq_price is not None else 0
