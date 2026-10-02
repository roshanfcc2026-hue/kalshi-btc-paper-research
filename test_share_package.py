"""Portable initialization and dashboard-asset checks; no network or live data."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bot
import init as initializer

ROOT = Path(__file__).resolve().parent


class SharePackageTests(unittest.TestCase):
    def test_initializer_is_offline_and_preserves_existing_data_and_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            shutil.copyfile(ROOT / 'config.example.json', folder / 'config.example.json')
            with patch.object(initializer, 'ROOT', folder), patch.object(bot, 'ROOT', folder), \
                    patch.object(bot, 'get', side_effect=AssertionError('Unexpected network')), \
                    patch.object(bot.live, 'fetch', side_effect=AssertionError('Unexpected network')), \
                    contextlib.redirect_stdout(io.StringIO()):
                initializer.main()
                settings = json.loads((folder / 'config.json').read_text())
                self.assertFalse(settings['openai_enabled'])
                self.assertFalse(settings['rules_and_fees_reviewed'])
                self.assertEqual(settings['min_training_markets'], 200)
                settings['poll_seconds'] = 19
                (folder / 'config.json').write_text(json.dumps(settings))
                with contextlib.closing(sqlite3.connect(folder / 'research.sqlite')) as db:
                    db.execute("INSERT INTO markets VALUES('KXBTC15M-FIXTURE','{}','')")
                    db.commit()
                initializer.main()
                self.assertEqual(json.loads((folder / 'config.json').read_text())['poll_seconds'], 19)
                with contextlib.closing(sqlite3.connect(folder / 'research.sqlite')) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM markets').fetchone()[0], 1)
                self.assertFalse((folder / 'midpoint-lock-v1').exists())
                self.assertFalse((folder / 'remaining-lock-v1').exists())

    def test_source_dashboard_has_no_embedded_history_and_loads_mount_order(self):
        html = (ROOT / 'dashboard.html').read_text(encoding='utf-8')
        for excluded in ('application/json', 'allocation-data', 'quant-shadow-data',
                         'timing-comparison-data', 'overnight-kpis'):
            self.assertNotIn(excluded, html)
        ordered = ('/analytics-workspace.js', '/midpoint-panel.js', '/remaining-panel.js')
        self.assertEqual(sorted(html.index(name) for name in ordered), [html.index(name) for name in ordered])


if __name__ == '__main__':
    unittest.main()
