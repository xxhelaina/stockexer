import unittest
from datetime import datetime

from models import TradingSimulator


class TradingRuleTests(unittest.TestCase):
    def test_t1_old_and_new_inventory(self):
        sim = TradingSimulator(100000, 0, allow_t0=False)
        sim.set_current_stock('600000')
        yesterday = datetime(2025, 11, 20, 10)
        today = datetime(2025, 11, 21, 10)
        sim.buy(10, 300, '测试', yesterday)
        sim.buy(10, 200, '测试', today)
        self.assertEqual(sim.get_sellable_hold(today), 300)
        self.assertIsNone(sim.sell(11, 400, '测试', today))
        sim.sell(11, 300, '测试', today)
        self.assertEqual(sim.get_sellable_hold(today), 0)
        self.assertEqual(sim.get_realized_pnl(), 300)
        self.assertEqual(sim.get_sellable_hold(datetime(2025, 11, 24, 10)), 200)

    def test_t0_fees_and_snapshot(self):
        sim = TradingSimulator(100000, .001, allow_t0=True)
        sim.set_current_stock('600000')
        day = datetime(2025, 11, 21, 10)
        sim.buy(10, 1000, '测试', day)
        sim.sell(11, 500, '测试', day)
        restored = TradingSimulator.from_snapshot(sim.to_snapshot())
        self.assertEqual(sim.to_snapshot(), restored.to_snapshot())
        self.assertAlmostEqual(sim.get_realized_pnl(), 489.5)
        self.assertAlmostEqual(sim.get_total_asset(11) - 100000,
                               sim.get_realized_pnl() + sim.get_unrealized_pnl(11))

    def test_invalid_orders_and_no_short(self):
        sim = TradingSimulator(10000)
        sim.set_current_stock('600000')
        for price, amount in [(10, 1), (float('nan'), 100), (10, 100000)]:
            self.assertFalse(sim.can_buy(price, amount)[0])
        self.assertFalse(sim.can_sell(10, 100)[0])


if __name__ == '__main__':
    unittest.main()
