import unittest

import numpy as np
import pandas as pd

from gui.precompute_thread import PrecomputeIndicatorsThread


class IndicatorTests(unittest.TestCase):
    def calculate(self, frame):
        output, errors = [], []
        thread = PrecomputeIndicatorsThread(frame)
        thread.finished.connect(output.append)
        thread.error.connect(errors.append)
        thread.run()
        self.assertEqual(errors, [])
        return output[0]

    def test_price_and_volume_moving_averages(self):
        frame = pd.DataFrame({'Close': np.arange(1., 321.), 'Volume': np.arange(10., 3210., 10.)},
                             index=pd.bdate_range('2025-01-01', periods=320))
        result = self.calculate(frame)
        for period in (5, 10, 20, 60, 120, 250):
            pd.testing.assert_series_equal(result[f'MA{period}'],
                frame.Close.rolling(period, min_periods=1).mean(), check_names=False)
        for period in (5, 10):
            pd.testing.assert_series_equal(result[f'VOLMA{period}'],
                frame.Volume.rolling(period, min_periods=1).mean(), check_names=False)

    def test_future_prices_do_not_change_prior_readouts(self):
        frame = pd.DataFrame({'Close': np.arange(1., 321.), 'Volume': np.arange(10., 3210., 10.)},
                             index=pd.bdate_range('2025-01-01', periods=320))
        original = self.calculate(frame)
        frame.iloc[101:] *= 100
        changed = self.calculate(frame)
        pd.testing.assert_frame_equal(original.iloc[:101], changed.iloc[:101])
