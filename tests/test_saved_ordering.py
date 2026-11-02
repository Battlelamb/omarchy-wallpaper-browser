# SPDX-License-Identifier: GPL-3.0-only
import unittest
import test_backend


class SavedOrderingTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown
    image = test_backend.BackendTests.image
    shim = test_backend.BackendTests.shim
    wallpaper_setters = test_backend.BackendTests.wallpaper_setters

    def test_favorite_beyond_real_cap_is_promoted_without_limiting_fresh_actions(self):
        files = [self.image('z%03d.png' % index) for index in range(257)]
        expected = ['wall:' + self.b.token(p) for p in files]
        self.assertEqual(self.b.MAX_ROWS, 256)
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], expected[:256])
        self.assertTrue(self.b.action(expected[-1], 'favorite', True)['ok'])
        self.b.preferences(True, {'options': {'sortOrder': 'favorites-first'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [expected[-1]] + expected[:255])
        self.assertFalse((self.home/'calls').exists())
        self.assertFalse(self.b.state_path().exists())
        self.wallpaper_setters()
        result = self.b.action(expected[255], 'apply', True)
        self.assertTrue(result['ok'], result)
        files[255].unlink()
        self.assertFalse(self.b.action(expected[255], 'favorite', True)['ok'])
        self.assertFalse(self.b.action(expected[255], 'apply', True)['ok'])

    def test_name_beyond_real_cap_is_sorted_before_truncation(self):
        files = [self.image('z%03d.png' % index) for index in range(256)]
        later = self.image('album/aardvark.png')
        expected = ['wall:' + self.b.token(p) for p in files]
        identity = 'wall:' + self.b.token(later)
        self.assertEqual(self.b.image_files(), files + [later])
        self.assertNotIn(identity, [r['id'] for r in self.b.catalog()['rows']])
        self.b.preferences(True, {'options': {'sortOrder': 'name'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [identity] + expected[:255])
        self.assertFalse((self.home/'calls').exists())
        self.assertFalse(self.b.state_path().exists())

    def test_default_and_lowercase_ordinal_ties_preserve_original_order(self):
        labels = ['Zulu.png', 'ss.png', 'zulu.png', 'ß.png', '\ue000.png', '\U00010000.png']
        for label in labels: self.image(label)
        original = self.b.catalog()['rows']
        self.assertEqual([r['label'] for r in original], labels)
        self.b.preferences(True, {'options': {'sortOrder': 'name'}})
        ordered = self.b.catalog()['rows']
        self.assertEqual([r['label'] for r in ordered], ['ss.png', 'Zulu.png', 'zulu.png', 'ß.png', '\U00010000.png', '\ue000.png'])
        self.assertEqual([r['id'] for r in ordered[1:3]], [original[0]['id'], original[2]['id']])
        favorites = [original[2]['id'], original[0]['id']]
        self.b.preferences(True, {'favorites': favorites, 'options': {'sortOrder': 'favorites-first'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [original[0]['id'], original[2]['id']] + [r['id'] for r in original if r['id'] not in favorites])
        self.b.preferences(True, {'options': {'sortOrder': 'default'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [r['id'] for r in original])
        self.assertFalse((self.home/'calls').exists())
        self.assertFalse(self.b.state_path().exists())
