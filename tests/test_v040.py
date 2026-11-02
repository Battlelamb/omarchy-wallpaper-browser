# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend


class DisplayOptionsTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown

    image = test_backend.BackendTests.image

    def test_file_type_filters_magic_and_jpeg_aliases_before_cap_with_existing_filters(self):
        self.assertEqual(self.b.PREFERENCE_SCHEMA.get('fileType', {}).get('choices'), ['all', 'png', 'jpeg', 'webp', 'bmp'])
        self.assertEqual(self.b.preferences()['preferences']['options']['fileType'], 'all')
        png = self.image('a.png')
        expected = {'png': [png.name], 'jpeg': [], 'webp': [], 'bmp': []}
        for coder, names in [('JPEG', ['b.jpg', 'c.jpeg', 'd.JPG', 'e.png']), ('WEBP', ['f.webp']), ('BMP', ['g.bmp'])]:
            code, data, err = self.b.run(['/usr/bin/magick', 'PNG:-', coder+':-'], payload=png.read_bytes(), timeout=3)
            self.assertEqual(code, 0, err)
            for name in names:
                (png.parent/name).write_bytes(data)
                expected[coder.lower()].append(name)
        (png.parent/'broken.jpg').write_bytes(b'not an image')
        (png.parent/'link.jpg').symlink_to(png.parent/'b.jpg')
        for kind, names in expected.items():
            self.b.preferences(True, {'options': {'fileType': kind}})
            rows = self.b.catalog()['rows']
            self.assertEqual([row['label'] for row in rows], names)
            self.assertEqual({row.get('fileType') for row in rows}, {kind})
            with test_backend.patch.object(self.b, 'MAX_ROWS', 1):
                self.assertEqual([row['label'] for row in self.b.catalog()['rows']], names[:1])
        folder = self.home/'Photography'; folder.mkdir()
        copied = folder/'extra.JPEG'; copied.write_bytes((png.parent/'b.jpg').read_bytes())
        self.b.preferences(True, {'options': {'folders': [str(folder)], 'fileType': 'jpeg'}})
        row = next(row for row in self.b.catalog()['rows'] if row['preview'] == str(copied))
        self.assertTrue(self.b.action(row['id'], 'favorite', True)['ok'])
        self.b.preferences(True, {'options': {'favoritesOnly': True, 'aspect': 'square', 'collection': 'Work'}, 'collections': {'Work': [row['id']]}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [row['id']])
        self.b.preferences(True, {'options': {'fileType': 'png'}})
        self.assertEqual(self.b.catalog()['rows'], [])
        self.assertTrue(self.b.action(row['id'], 'favorite', True)['ok'])
        before = self.b.preferences()['preferences']
        for bad in (None, True, 1, [], {}, 'jpg', 'JPEG', 'gif', 'remote'):
            with self.assertRaises(ValueError): self.b.preferences(True, {'options': {'fileType': bad}})
            self.assertEqual(self.b.preferences()['preferences'], before)
        self.assertEqual(self.b.preferences(True, {'reset': True})['preferences']['options']['fileType'], 'all')
        self.assertFalse((self.home/'calls').exists())
        self.assertFalse(self.b.state_path().exists())

    shim = test_backend.BackendTests.shim
    wallpaper_setters = test_backend.BackendTests.wallpaper_setters

    def test_file_type_filter_does_not_remove_restore_or_change_action_membership(self):
        first = self.image('first.png'); self.image('second.png', (0, 255, 0))
        self.wallpaper_setters()
        rows = self.b.catalog()['rows']
        for row in rows: self.assertTrue(self.b.action(row['id'], 'apply', True)['ok'])
        self.b.preferences(True, {'options': {'fileType': 'jpeg'}})
        value = self.b.catalog()
        self.assertEqual(value['rows'], [])
        self.assertIn('restore', value['actions'])
        self.assertFalse(self.b.action(rows[0]['id'], 'shuffle', True)['ok'])
        result = self.b.action('restore:last', 'restore', True)
        self.assertTrue(result['ok'], result)
        self.assertTrue(self.b.action(rows[1]['id'], 'apply', True)['ok'])
        first.write_bytes(test_backend.png((0, 0, 255)))
        self.assertFalse(self.b.action(rows[0]['id'], 'apply', True)['ok'])

    def test_legacy_options_gain_defaults_without_read_time_write(self):
        value = self.b.preferences()['preferences']
        for key in ('density', 'layout', 'sortOrder', 'themeSource', 'fileType'):
            value['options'].pop(key, None)
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        store.parent.mkdir(parents=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(value).encode(); store.write_bytes(raw); store.chmod(0o600)
        defaults = self.b.preferences()['preferences']['options']
        self.assertEqual(defaults['density'], 'comfortable')
        self.assertEqual(defaults['layout'], 'grid')
        self.assertEqual(defaults['sortOrder'], 'default')
        if self.b.PLUGIN == 'theme-gallery': self.assertEqual(defaults['themeSource'], 'all')
        if self.b.PLUGIN == 'wallpaper-browser': self.assertEqual(defaults['fileType'], 'all')
        self.assertEqual(store.read_bytes(), raw)

    def test_common_display_schema_roundtrip_rejection_is_atomic_and_reset(self):
        expected = {
            'density': ('comfortable', ['comfortable', 'compact']),
            'layout': ('grid', ['grid', 'list']),
            'sortOrder': ('default', ['default', 'name', 'favorites-first']),
        }
        initial = self.b.preferences()
        for key, (default, choices) in expected.items():
            self.assertIn(key, initial['settingsSchema'])
            rule = initial['settingsSchema'][key]
            self.assertEqual(rule['type'], 'enum')
            self.assertEqual(rule['default'], default)
            self.assertEqual(rule['choices'], choices)
            self.assertEqual(initial['preferences']['options'][key], default)
        options = {'density': 'compact', 'layout': 'list', 'sortOrder': 'favorites-first'}
        value = self.b.preferences(True, {'options': options})
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        before = store.read_bytes()
        for key in options:
            for bad in (None, True, 1, [], {}, 'invalid', '界'*6000):
                with self.assertRaises(ValueError):
                    self.b.preferences(True, {'options': {key: bad}})
                self.assertEqual(store.read_bytes(), before)
        result = subprocess.run(['/usr/bin/python3', str(test_backend.BACKEND), 'preferences'], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout)
        saved = json.loads(result.stdout)['preferences']['options']
        self.assertEqual({key: saved[key] for key in options}, options)
        reset = self.b.preferences(True, {'reset': True})['preferences']['options']
        for key, (default, _) in expected.items():
            self.assertEqual(reset[key], default)
