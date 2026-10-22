#!/usr/bin/env python3
"""Corrupt or disabled optional themes must return to Steam without root writes."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('plugin', ROOT / 'main.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class ThemeFallback(unittest.TestCase):
    def test_shared_theme_contract(self):
        f=json.loads((ROOT / 'scripts/fixtures/theme-v1.json').read_text())
        self.assertEqual(m._theme_document_validate(f['valid']),f['valid'])
        for value in f['invalidPatches']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m._theme_document_validate(dict(f['valid'],**value))

    def test_tv_write_rejects_untrusted_fields_before_transport(self):
        plugin = m.Plugin()
        with patch.object(m.urllib.request, 'urlopen') as request:
            for value in [None, [], {}, {'installed': True}, {'url': 'https://example.com'},
                          {'theme': []}, {'theme': '../../x'}, {'motion': 1}, {'enabled': 'true'}]:
                self.assertFalse(asyncio.run(plugin.set_steam_theme(value))['ok'])
            request.assert_not_called()

    def test_tv_write_uses_fixed_local_authenticated_endpoint(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"settings":{"enabled":false}}'
        with patch.object(m, '_read_token', return_value='private-test-token'), \
             patch.object(m, '_read_port', return_value=8787), \
             patch.object(m.urllib.request, 'urlopen', return_value=Response()) as request:
            result = asyncio.run(m.Plugin().set_steam_theme({'enabled': False}))
            self.assertTrue(result['ok'])
            req = request.call_args.args[0]
            self.assertEqual(req.full_url, 'http://127.0.0.1:8787/api/steam/themes')
            self.assertEqual(req.get_header('Authorization'), 'Bearer private-test-token')
            self.assertEqual(json.loads(req.data), {'enabled': False})
            self.assertNotIn('private-test-token', json.dumps(result))

    def test_console_layouts_use_same_validated_write(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"settings":{}}'
        with patch.object(m, '_read_token', return_value='private-test-token'), \
             patch.object(m, '_read_port', return_value=8787), \
             patch.object(m.urllib.request, 'urlopen', return_value=Response()):
            for theme in ['wave', 'channels']:
                self.assertTrue(asyncio.run(m.Plugin().set_steam_theme({'theme': theme, 'enabled': True}))['ok'])

    def test_runtime_filter(self):
        for name in ["Proton 9.0", "Proton Experimental", "Proton Hotfix", "Steam Linux Runtime 2.0 (soldier)", "Steamworks Common Redistributables"]:
            self.assertTrue(m.Plugin._theme_runtime(name), name)
        for name in ["Portal 2", "Proton Pulse", "SteamWorld Dig", "A Game About Digging a Hole"]:
            self.assertFalse(m.Plugin._theme_runtime(name), name)

    def test_disabled_corrupt_and_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            m.STATE_DIR = temp
            path = Path(temp) / 'steam-themes.json'
            plugin = m.Plugin()
            for value in [None, '{', 'x'*4097, json.dumps({'enabled': True}), json.dumps({'version': 1, 'installed': False, 'enabled': True, 'theme': 'couchside', 'accent': 'teal', 'motion': True})]:
                if value is not None: path.write_text(value)
                result = asyncio.run(plugin.get_steam_theme())
                self.assertFalse(result['settings']['enabled']); self.assertEqual(result['games'], [])
            path.unlink()
            target = Path(temp) / 'secret'; target.write_text('do not read')
            path.symlink_to(target)
            self.assertFalse(asyncio.run(plugin.get_steam_theme())['settings']['enabled'])
            self.assertEqual(target.read_text(), 'do not read')
    def test_valid_preserved_without_library(self):
        with tempfile.TemporaryDirectory() as temp:
            m.STATE_DIR = temp
            value = {'version': 1, 'installed': True, 'enabled': True, 'theme': 'orbit', 'accent': 'violet', 'motion': False}
            path = Path(temp) / 'steam-themes.json'; path.write_text(json.dumps(value))
            m._target_user = lambda: 'nonexistent-user-for-test'
            result = asyncio.run(m.Plugin().get_steam_theme())
            self.assertEqual(result['settings'], dict(value, studio=None)); self.assertEqual(result['games'], [])
            self.assertEqual(json.loads(path.read_text()), value)

if __name__ == '__main__': unittest.main()
