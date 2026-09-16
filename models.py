# -*- coding: utf-8 -*-
"""数据模型和交易模拟器"""
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import logging
import math

logger = logging.getLogger(__name__)


class TradeAction(Enum):
    BUY = "买入"
    SELL = "卖出"


@dataclass
class TradeRecord:
    date: datetime
    stock_code: str
    stock_name: str
    action: TradeAction
    price: float
    amount: int
    fee: float
    net_amount: float
    realized_pnl: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'date': self.date,
            'stock_code': self.stock_code,
            'stock_name': self.stock_name,
            'action': self.action.value,
            'price': self.price,
            'amount': self.amount,
            'fee': self.fee,
            'net_amount': self.net_amount,
            'realized_pnl': self.realized_pnl
        }


class TradingSimulator:
    """交易模拟器，使用Decimal处理资金避免浮点误差"""

    def __init__(self, initial_capital: float = 1000000.0, fee_rate: float = 0.003,
                 allow_t0: bool = True):
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError('初始资金必须是大于0的有限数值')
        if not math.isfinite(fee_rate) or not 0 <= fee_rate <= 1:
            raise ValueError('费率必须在0到1之间')
        self.initial_capital = Decimal(str(initial_capital))
        self.fee_rate = Decimal(str(fee_rate))
        self.allow_t0 = allow_t0
        self.reset()

    def reset(self) -> None:
        self.current_capital = self.initial_capital
        self.positions: Dict[str, int] = {}
        self.position_costs: Dict[str, Decimal] = {}
        self.realized_pnl: Dict[str, Decimal] = {}
        self.trade_history: List[TradeRecord] = []
        self.current_stock: Optional[str] = None
        self.purchases_by_day = {}

    def set_current_stock(self, stock_code: str) -> None:
        self.current_stock = stock_code
        if stock_code not in self.positions:
            self.positions[stock_code] = 0
            self.position_costs[stock_code] = Decimal('0')
            self.realized_pnl[stock_code] = Decimal('0')

    def can_buy(self, price: float, amount: int) -> Tuple[bool, str]:
        if not self.current_stock:
            return False, "未设置当前股票"
        if not math.isfinite(price) or price <= 0 or amount <= 0 or int(amount) != amount:
            return False, "价格和数量必须大于0"
        if amount % 100:
            return False, '本训练采用简化100股一手规则，买入数量须为100的整数倍'
        price_dec = Decimal(str(price))
        total_cost = price_dec * amount * (1 + self.fee_rate)
        if total_cost > self.current_capital:
            return False, f"资金不足，需要{total_cost:.2f}元，可用{float(self.current_capital):.2f}元"
        return True, ""

    def buy(self, price: float, amount: int,
            stock_name: str, trade_date: datetime) -> Optional[TradeRecord]:
        can_buy, error_msg = self.can_buy(price, amount)
        if not can_buy:
            logger.warning(f"买入失败: {error_msg}")
            return None
        price_dec = Decimal(str(price))
        total_cost = price_dec * amount * (1 + self.fee_rate)
        fee = price_dec * amount * self.fee_rate
        old_amount = self.positions[self.current_stock]
        old_cost = self.position_costs.get(self.current_stock, Decimal('0'))
        new_amount = old_amount + amount
        # 持仓成本包含买入手续费，和账户权益变化保持一致。
        self.position_costs[self.current_stock] = (
            old_cost * old_amount + total_cost) / new_amount
        self.current_capital -= total_cost
        self.positions[self.current_stock] = new_amount
        day_key = (self.current_stock, trade_date.date())
        self.purchases_by_day[day_key] = self.purchases_by_day.get(day_key, 0) + amount
        record = TradeRecord(
            date=trade_date,
            stock_code=self.current_stock,
            stock_name=stock_name,
            action=TradeAction.BUY,
            price=price,
            amount=amount,
            fee=float(fee),
            net_amount=-float(total_cost)
        )
        self.trade_history.append(record)
        logger.info(f"买入 {stock_name}({self.current_stock}) {amount}股 @ {price:.2f}")
        return record

    def get_sellable_hold(self, trade_date: datetime) -> int:
        hold = self.get_current_hold()
        if self.allow_t0:
            return hold
        return max(0, hold - self.purchases_by_day.get((self.current_stock, trade_date.date()), 0))

    def can_sell(self, price: float, amount: int, trade_date: datetime = None) -> Tuple[bool, str]:
        if not self.current_stock:
            return False, "未设置当前股票"
        if not math.isfinite(price) or price <= 0 or amount <= 0 or int(amount) != amount:
            return False, "价格和数量必须大于0"
        current_hold = self.positions.get(self.current_stock, 0)
        if amount > current_hold:
            return False, f"持仓不足，当前持仓{current_hold}股，试图卖出{amount}股"
        if not self.allow_t0:
            if trade_date is None:
                return False, 'T+1校验需要交易日期'
            sellable = self.get_sellable_hold(trade_date)
            if amount > sellable:
                return False, f'T+1限制：今日买入不可卖出，当前可卖 {sellable} 股'
        return True, ""

    def sell(self, price: float, amount: int,
             stock_name: str, trade_date: datetime) -> Optional[TradeRecord]:
        can_sell, error_msg = self.can_sell(price, amount, trade_date)
        if not can_sell:
            logger.warning(f"卖出失败: {error_msg}")
            return None
        price_dec = Decimal(str(price))
        total_revenue = price_dec * amount * (1 - self.fee_rate)
        fee = price_dec * amount * self.fee_rate
        average_cost = self.position_costs.get(self.current_stock, Decimal('0'))
        realized = total_revenue - average_cost * amount
        self.current_capital += total_revenue
        self.positions[self.current_stock] -= amount
        self.realized_pnl[self.current_stock] = (
            self.realized_pnl.get(self.current_stock, Decimal('0')) + realized)
        if self.positions[self.current_stock] == 0:
            self.position_costs[self.current_stock] = Decimal('0')
        record = TradeRecord(
            date=trade_date,
            stock_code=self.current_stock,
            stock_name=stock_name,
            action=TradeAction.SELL,
            price=price,
            amount=amount,
            fee=float(fee),
            net_amount=float(total_revenue),
            realized_pnl=float(realized),
        )
        self.trade_history.append(record)
        logger.info(f"卖出 {stock_name}({self.current_stock}) {amount}股 @ {price:.2f}")
        return record

    def get_total_asset(self, current_price: float) -> float:
        hold_amount = self.positions.get(self.current_stock, 0)
        total = self.current_capital + (Decimal(str(current_price)) * hold_amount)
        return float(total)

    def get_current_hold(self) -> int:
        return self.positions.get(self.current_stock, 0)

    def get_average_cost(self) -> float:
        return float(self.position_costs.get(self.current_stock, Decimal('0')))

    def get_market_value(self, current_price: float) -> float:
        return float(Decimal(str(current_price)) * self.get_current_hold())

    def get_unrealized_pnl(self, current_price: float) -> float:
        hold = self.get_current_hold()
        average_cost = self.position_costs.get(self.current_stock, Decimal('0'))
        return float((Decimal(str(current_price)) - average_cost) * hold)

    def get_realized_pnl(self) -> float:
        return float(self.realized_pnl.get(self.current_stock, Decimal('0')))

    def to_snapshot(self):
        records = []
        for record in self.trade_history:
            item = record.to_dict()
            item['date'] = record.date.isoformat()
            records.append(item)
        return {'initial_capital': format(self.initial_capital.normalize(), 'f'),
                'fee_rate': format(self.fee_rate.normalize(), 'f'),
                'allow_t0': self.allow_t0, 'stock_code': self.current_stock, 'trades': records}

    @classmethod
    def from_snapshot(cls, snapshot):
        if not isinstance(snapshot['allow_t0'], bool):
            raise ValueError('存档交易规则错误')
        simulator = cls(float(snapshot['initial_capital']), float(snapshot['fee_rate']),
                        bool(snapshot['allow_t0']))
        simulator.set_current_stock(snapshot['stock_code'])
        previous = None
        for item in snapshot['trades']:
            day = datetime.fromisoformat(item['date'])
            if previous is not None and day < previous:
                raise ValueError('存档成交时间顺序错误')
            previous = day
            if item['stock_code'] != simulator.current_stock:
                raise ValueError('存档股票与成交记录不一致')
            if item['action'] not in ('买入', '卖出'):
                raise ValueError('存档交易方向错误')
            if int(item['amount']) != item['amount'] or item['amount'] <= 0:
                raise ValueError('存档成交数量错误')
            method = simulator.buy if item['action'] == '买入' else simulator.sell
            if method(float(item['price']), int(item['amount']), item['stock_name'], day) is None:
                raise ValueError('存档包含不合法的交易')
        return simulator

    @property
    def current_capital_float(self) -> float:
        return float(self.current_capital)
