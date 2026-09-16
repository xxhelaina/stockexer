import unittest

import pandas as pd

from gui.synthesize_thread import SynthesizePeriodThread


class PeriodSynthesisTests(unittest.TestCase):
    def synthesize(self, index, period):
        frame = pd.DataFrame({'Open': 10., 'High': 11., 'Low': 9.,
                              'Close': 10., 'Volume': 100}, index=pd.to_datetime(index))
        thread = SynthesizePeriodThread(frame, period)
        output, errors = [], []
        thread.finished.connect(lambda data, _: output.append(data))
        thread.error.connect(errors.append)
        thread.run()
        self.assertFalse(errors)
        return output[0]

    def test_minute_end_timestamps_and_opening_alignment(self):
        result = self.synthesize(['2025-11-21 09:35', '2025-11-21 09:40',
                                  '2025-11-21 09:45', '2025-11-21 09:50'], '20min')
        self.assertEqual(result.index[0], pd.Timestamp('2025-11-21 09:50'))
        self.assertEqual(result.Volume.iloc[0], 400)
        result = self.synthesize(['2025-11-21 09:35', '2025-11-21 09:40'], '5min')
        self.assertEqual(result.index[0], pd.Timestamp('2025-11-21 09:35'))
        self.assertEqual(len(result), 2)

    def test_week_and_month_end_labels(self):
        index = ['2025-11-17', '2025-11-18', '2025-11-21']
        self.assertEqual(self.synthesize(index, 'W').index[0], pd.Timestamp('2025-11-21'))
        self.assertEqual(self.synthesize(index, 'M').index[0], pd.Timestamp('2025-11-30'))


if __name__ == '__main__':
    unittest.main()
