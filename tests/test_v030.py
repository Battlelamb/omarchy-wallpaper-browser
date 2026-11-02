# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend

class FeatureTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown
    theme = test_backend.BackendTests.theme
    image = test_backend.BackendTests.image
    shim = test_backend.BackendTests.shim
    wallpaper_setters = test_backend.BackendTests.wallpaper_setters

    def cli(self,*args):
        return subprocess.run(['/usr/bin/python3',str(test_backend.BACKEND),*args],capture_output=True,text=True,timeout=8,cwd='/')

    def test_saved_local_folder_collections_and_aspect_filters(self):
        folder=self.home/'Photography';folder.mkdir()
        wall=folder/'wide.png';data=bytearray(test_backend.png());data[16:24]=test_backend.struct.pack('!II',32,9);wall.write_bytes(data)
        self.assertFalse(any(r['preview']==str(wall) for r in self.b.catalog()['rows']))
        result=self.cli('preferences','--apply','--payload',json.dumps({'options':{'folders':[str(folder)]}}))
        self.assertEqual(result.returncode,0,result.stdout)
        row=next(r for r in self.b.catalog()['rows'] if r['preview']==str(wall))
        self.assertEqual(row['category'],'ultrawide');self.assertIn('32 × 9',row['detail'])
        self.b.preferences(True,{'collections':{'Work':[row['id']]},'options':{'collection':'Work','aspect':'ultrawide'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']],[row['id']])
        self.b.preferences(True,{'options':{'aspect':'portrait'}})
        self.assertEqual(self.b.catalog()['rows'],[])
        link=self.home/'link';link.symlink_to(folder,target_is_directory=True)
        for folders in ([str(link)],['relative'],[str(folder/'..')],['/'],['x\nfolder'],[str(folder)]*33):
            self.assertNotEqual(self.cli('preferences','--apply','--payload',json.dumps({'options':{'folders':folders}})).returncode,0,folders)
        wall.unlink();wall.symlink_to(self.image())
        self.b.preferences(True,{'options':{'aspect':'all'}})
        self.assertEqual(self.b.catalog()['rows'],[])

    def test_shuffle_only_selects_filtered_member_without_setter_or_receipt(self):
        self.image('one.png');self.image('two.png')
        rows=self.b.catalog()['rows'];selected=rows[0]['id']
        with test_backend.patch.object(self.b,'run',side_effect=AssertionError('Shuffle must not spawn a setter')):
            result=self.b.action(selected,'shuffle',True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['selectedId'],rows[1]['id'])
        self.assertIn('not applied',result['message'].lower())
        self.assertFalse(self.b.journal())
        self.assertEqual(self.b.current_background(),'')
        self.assertFalse(self.b.action('wall:missing','shuffle',True)['ok'])
        self.assertFalse(self.b.action(selected,'shuffle')['ok'])
        self.b.preferences(True,{'collections':{'One':[selected]},'options':{'collection':'One'}})
        self.assertEqual(self.b.action(selected,'shuffle',True)['selectedId'],selected)

    def test_recent_is_saved_only_after_confirmed_apply(self):
        wall=self.image();selected=self.b.catalog()['rows'][0]['id']
        self.assertFalse(self.b.action(selected,'apply',True)['ok'])
        self.assertEqual(self.b.preferences()['preferences']['recent'],[])
        self.wallpaper_setters()
        result=self.b.action(selected,'apply',True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result.get('preferences',{}).get('recent'),[selected])
        self.image('other.png',(0,255,0));other=next(r['id'] for r in self.b.catalog()['rows'] if r['id']!=selected)
        with test_backend.patch.object(self.b.prefs_module(),'remember',side_effect=ValueError('full')):
            result=self.b.action(other,'apply',True)
        self.assertTrue(result['ok'],result);self.assertIn('could not be saved',result['message'])

    def test_encoded_dimensions_for_installed_local_codecs_and_unknown_headers(self):
        wall=self.image()
        for coder,suffix in (('JPEG','jpg'),('WEBP','webp'),('BMP','bmp')):
            code,data,err=self.b.run(['/usr/bin/magick','PNG:-',coder+':-'],payload=wall.read_bytes(),timeout=3)
            self.assertEqual(code,0,err)
            path=wall.with_suffix('.'+suffix);path.write_bytes(data)
            self.assertEqual(self.b.dimensions(path),(2,2,'square'))
        self.assertEqual(self.b.dimensions(wall),(2,2,'square'))
        path=wall.parent/'unknown.jpg';path.write_bytes(b'\xff\xd8\xff\xe0\x00\x01')
        self.assertEqual(self.b.dimensions(path),(0,0,'unknown'))
        for width,height,category in ((3,2,'landscape'),(2,3,'portrait'),(16,9,'landscape'),(21,9,'ultrawide')):
            data=bytearray(wall.read_bytes());data[16:24]=test_backend.struct.pack('!II',width,height);path=wall.parent/'shape.png';path.write_bytes(data)
            self.assertEqual(self.b.dimensions(path),(width,height,category))
        path.write_bytes(b'RIFF'+b'\0'*4+b'WEBPVP8X'+b'\0'*4+b'\0'*4+b'\x03\0\0'+b'\x01\0\0')
        self.assertEqual(self.b.dimensions(path),(4,2,'ultrawide'))

    def test_documented_cli_collection_shuffle_apply_restore_roundtrip(self):
        self.image('one.png');self.image('two.png',(0,255,0));self.wallpaper_setters()
        rows=json.loads(self.cli('catalog').stdout)['rows'];selected=rows[0]['id']
        result=self.cli('action','--id',selected,'--verb','shuffle','--apply')
        self.assertEqual(result.returncode,0,result.stdout);shuffled=json.loads(result.stdout)['selectedId']
        for ident in (selected,shuffled):
            result=self.cli('action','--id',ident,'--verb','apply','--apply');self.assertEqual(result.returncode,0,result.stdout)
        result=self.cli('action','--id','restore:last','--verb','restore','--apply');self.assertEqual(result.returncode,0,result.stdout)
        result=self.cli('preferences','--apply','--payload',json.dumps({'collections':{'CLI':[selected]},'options':{'collection':'CLI'}}))
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual([r['id'] for r in json.loads(self.cli('catalog').stdout)['rows']],[selected])

    def test_saved_preferences_and_favorites_validate_fresh_membership(self):
        self.image()
        selected=self.b.catalog()['rows'][0]['id']
        result=self.b.action(selected,'favorite',True)
        self.assertTrue(result['ok'],result)
        self.assertTrue(self.b.catalog()['rows'][0]['favorite'])
        self.assertFalse(self.b.action('wall:missing','favorite',True)['ok'])
        result=self.cli('preferences','--apply','--payload','{"options":{"favoritesOnly":true}}')
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual(json.loads(result.stdout)['preferences']['favorites'],[selected])
        for args in [('catalog','--apply'),('catalog','--payload','{}'),('preferences','--payload','{}'),('preferences','--id','x'),('action','--id',selected,'--verb','favorite','--apply','--payload','{}')]:
            self.assertNotEqual(self.cli(*args).returncode,0,args)
        for raw in ('[]','{"options":{},"options":{}}','{"options":{"aspect":NaN}}','{"options":{"aspect":"bad"}}','{"version":1}','{"reset":true,"options":{}}'):
            self.assertNotEqual(self.cli('preferences','--apply','--payload',raw).returncode,0,raw)
        self.assertFalse(self.b.action(selected,'favorite')['ok'])
        result=self.cli('preferences','--apply','--payload','{"reset":true}')
        self.assertEqual(json.loads(result.stdout)['preferences']['favorites'],[])
