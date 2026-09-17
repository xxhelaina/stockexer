import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app_paths


class AppPathTests(unittest.TestCase):
    def tearDown(self):
        app_paths.data_dir.cache_clear()

    def test_source_paths_are_not_relative_to_working_directory(self):
        self.assertEqual(app_paths.application_dir(), Path(app_paths.__file__).resolve().parent)
        self.assertEqual(app_paths.download_dir(), app_paths.application_dir() / '下载数据')

    def test_frozen_data_is_next_to_executable_not_internal(self):
        with tempfile.TemporaryDirectory() as folder, patch('app_paths.sys.frozen', True, create=True), patch(
                'app_paths.sys.executable', str(Path(folder) / 'StockLab.exe')):
            app_paths.data_dir.cache_clear()
            self.assertEqual(app_paths.data_dir(), Path(folder) / 'data')
            self.assertTrue(app_paths.data_dir().is_dir())
            self.assertIn(str(Path(folder) / 'data' / '下载数据'), app_paths.local_search_roots())

    def test_readonly_application_falls_back_to_localappdata(self):
        with tempfile.TemporaryDirectory() as folder, patch('app_paths.sys.frozen', True, create=True), patch.dict(
                os.environ, {'LOCALAPPDATA': folder}), patch(
                'app_paths._writable_directory', side_effect=[PermissionError('readonly'), Path(folder) / 'StockLab']):
            app_paths.data_dir.cache_clear()
            self.assertEqual(app_paths.data_dir(), Path(folder) / 'StockLab')
