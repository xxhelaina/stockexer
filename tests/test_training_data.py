import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from training_data import LocalDataMissing, convert_period, iter_market_files, range_covered, resolve_training_data
from online_data import code_to_secid, code_to_tencent_symbol, code_to_baostock_symbol
from online_data import download_kline, _bounded_baostock, fetch_stock_universe
from unittest.mock import MagicMock


def minute_frame():
    index = pd.date_range('2025-11-21 09:35', periods=24, freq='5min').append(
        pd.date_range('2025-11-21 13:05', periods=24, freq='5min'))
    prices = np.linspace(20, 22, len(index))
    return pd.DataFrame({'Open': prices, 'High': prices + .1, 'Low': prices - .1,
                         'Close': prices, 'Volume': 1000}, index=index)


class TrainingDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.request = {'random': False, 'code': '600000', 'period': '5min',
                        'start': pd.Timestamp('2025-11-21 10:00'),
                        'end': pd.Timestamp('2025-11-21 15:00')}
        self.frame = minute_frame()

    def cache(self, code='600000', frame=None):
        path = os.path.join(self.directory.name, f'{code}_5min.csv')
        (self.frame if frame is None else frame).to_csv(path)
        return path

    def resolve(self, **kwargs):
        return resolve_training_data(self.request, [self.directory.name], **kwargs)

    def test_cache_hit_is_offline_and_keeps_warmup(self):
        self.cache()
        with patch('training_data.download_kline') as download:
            frame, meta = self.resolve()
            download.assert_not_called()
        self.assertEqual(meta['code'], '600000')
        self.assertEqual(frame.index[0], self.frame.index[0])
        self.assertEqual(frame.index[-1], self.request['end'])

    def test_specific_code_does_not_use_last_imported_stock(self):
        self.cache('000001')
        memory = (self.frame, '000001', '错误股票', '5min', '当前数据')
        with patch('training_data.download_kline', return_value=(self.frame, '浦发银行', 'mock')) as download:
            _, meta = self.resolve(memory=memory, allow_online=True)
        self.assertEqual(meta['code'], '600000')
        self.assertEqual(download.call_args.args[0], '600000')

    def test_missing_or_partial_cache_downloads_requested_range(self):
        self.cache(frame=self.frame.iloc[:24])
        with patch('training_data.download_kline', return_value=(self.frame, '浦发银行', 'mock')) as download:
            _, meta = self.resolve(allow_online=True)
        self.assertTrue(meta['origin'].startswith('在线'))
        self.assertEqual(download.call_args.kwargs['required_range'],
                         (self.request['start'], self.request['end']))
        self.assertLess(pd.Timestamp(download.call_args.args[2]), self.request['start'])

    def test_download_with_insufficient_history_is_rejected(self):
        with patch('training_data.download_kline', return_value=(self.frame.iloc[:24], '浦发银行', 'mock')):
            with self.assertRaisesRegex(ValueError, '未覆盖'):
                self.resolve(allow_online=True)

    def test_random_stock_uses_local_pool_before_network(self):
        self.cache('000001')
        self.request['random'] = True
        memory = (self.frame, '600000', '最后导入', '5min', '当前数据')
        with patch('training_data.download_kline') as download, patch('training_data.fetch_stock_universe') as universe:
            _, meta = self.resolve(memory=memory)
        self.assertEqual(meta['code'], '000001')
        download.assert_not_called()
        universe.assert_not_called()

    def test_random_stock_without_local_pool_gets_online_list(self):
        self.request['random'] = True
        with patch('training_data.fetch_stock_universe', return_value={'600000': '浦发银行'}) as universe, patch(
                'training_data.download_kline', return_value=(self.frame, '', 'mock')):
            _, meta = self.resolve(allow_online=True)
        universe.assert_called_once()
        self.assertEqual(meta['code'], '600000')

    def test_missing_local_data_never_downloads_without_permission(self):
        for random_mode in (False, True):
            self.request['random'] = random_mode
            with self.subTest(random=random_mode), patch('training_data.download_kline') as download, patch(
                    'training_data.fetch_stock_universe') as universe:
                with self.assertRaises(LocalDataMissing):
                    self.resolve()
                download.assert_not_called()
                universe.assert_not_called()

    def test_partial_local_data_requires_permission(self):
        self.cache(frame=self.frame.iloc[:24])
        with patch('training_data.download_kline') as download:
            with self.assertRaises(LocalDataMissing):
                self.resolve()
            download.assert_not_called()

    def test_compatible_local_period_can_be_aggregated(self):
        self.cache()
        self.request['period'] = '15min'
        with patch('training_data.download_kline') as download:
            frame, meta = self.resolve()
        download.assert_not_called()
        self.assertEqual(meta['period'], '15min')
        self.assertEqual(meta['source_period'], '5min')
        self.assertEqual(frame.iloc[0].Volume, 3000)

    def test_finer_period_cannot_be_invented_from_local_5min(self):
        with self.assertRaises(ValueError):
            convert_period(self.frame, '5min', '1min')

    def test_end_time_trims_all_future_bars(self):
        self.cache()
        self.request['end'] = pd.Timestamp('2025-11-21 11:00')
        frame, meta = self.resolve()
        self.assertEqual(frame.index[-1], self.request['end'])
        self.assertTrue((meta['raw'].index <= self.request['end']).all())

    def test_weekend_and_lunch_are_not_missing_bars(self):
        self.assertTrue(range_covered(self.frame, '2025-11-21 09:35', '2025-11-23 23:59', '5min'))
        self.assertTrue(range_covered(self.frame.iloc[:24], '2025-11-21 09:35', '2025-11-21 12:00', '5min'))

    def test_cancelled_scan_does_not_download(self):
        with patch('training_data.download_kline') as download:
            with self.assertRaises(InterruptedError):
                self.resolve(cancelled=lambda: True)
        download.assert_not_called()

    def test_scan_deduplicates_registered_paths(self):
        path = self.cache()
        self.assertEqual(list(iter_market_files([self.directory.name], [path, path])), [path])

    def test_directory_scan_includes_cached_csv_files(self):
        from gui.scan_thread import ScanFolderThread
        self.cache()
        results = []
        thread = ScanFolderThread(self.directory.name)
        thread.finished.connect(lambda files, names: results.append(files))
        thread.run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['600000_5min.csv'][1:], ('600000', 'csv'))

    def test_beijing_92_prefix_is_not_mapped_to_shanghai(self):
        self.assertEqual(code_to_secid('920000'), '0.920000')
        self.assertEqual(code_to_tencent_symbol('920000'), 'bj920000')
        self.assertEqual(code_to_baostock_symbol('920000'), 'bj.920000')
        self.assertEqual(code_to_tencent_symbol('600000'), 'sh600000')
        self.assertEqual(code_to_tencent_symbol('000001'), 'sz000001')

    def test_online_source_with_partial_range_tries_next_provider(self):
        with patch('online_data.fetch_kline_baostock', return_value=(self.frame.iloc[:24], '短数据')), patch(
                'online_data.fetch_kline_eastmoney', return_value=(self.frame, '浦发银行')) as eastmoney, patch(
                'online_data.download_dir', return_value=os.path.join(self.directory.name, '下载数据')):
            frame, name, source = download_kline('600000', '5min', '20251101', '20251121',
                required_range=(self.request['start'], self.request['end']))
        eastmoney.assert_called_once()
        self.assertEqual(source, '东方财富')
        self.assertEqual(len(frame), 48)

    def test_short_download_does_not_overwrite_long_cache(self):
        with patch('online_data.fetch_kline_baostock', side_effect=[(self.frame, '浦发银行'),
                       (self.frame.iloc[:24], '浦发银行')]), patch(
                'online_data.download_dir', return_value=os.path.join(self.directory.name, '下载数据')):
            download_kline('600000', '5min', '20251101', '20251121')
            download_kline('600000', '5min', '20251101', '20251121')
        folder = os.path.join(self.directory.name, '下载数据')
        self.assertEqual(len(os.listdir(folder)), 2)
        row_counts = sorted(len(pd.read_csv(os.path.join(folder, name))) for name in os.listdir(folder))
        self.assertEqual(row_counts, [24, 48])

    def test_baostock_timeout_terminates_only_owned_process(self):
        context, reader, writer, process = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        context.Pipe.return_value = reader, writer
        context.Process.return_value = process
        reader.poll.return_value = False
        process.pid = 123
        process.is_alive.return_value = True
        with patch('online_data.multiprocessing.get_context', return_value=context), patch(
                'importlib.util.find_spec', return_value=True):
            with self.assertRaises(TimeoutError):
                _bounded_baostock('kline', (), timeout=.01)
        process.terminate.assert_called_once()
        reader.close.assert_called_once()

    def test_universe_fallback_paginates_current_em_list(self):
        first, second = MagicMock(), MagicMock()
        first.json.return_value = {'data': {'total': 2, 'diff': [{'f12': '600000', 'f14': '浦发银行'}]}}
        second.json.return_value = {'data': {'total': 2, 'diff': [{'f12': '000001', 'f14': '平安银行'}]}}
        with patch('online_data._bounded_baostock', side_effect=TimeoutError('test timeout')), patch(
                'online_data._request_with_retry', side_effect=[first, second]) as get:
            result = fetch_stock_universe('2025-11-21')
        self.assertEqual(result, {'600000': '浦发银行', '000001': '平安银行'})
        self.assertEqual(get.call_count, 2)
