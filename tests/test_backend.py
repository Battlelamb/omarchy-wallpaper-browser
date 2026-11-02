# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / Path(json.loads((ROOT / 'manifest.json').read_text())['entryPoints']['overlay']).parent / 'backend.py'
def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*2)*2))+chunk(b'IEND',b'')

class BackendTests(unittest.TestCase):
    def test_folder_schema_and_readme_describe_actual_json_array_editor(self):
        self.assertIn('JSON array', self.b.PREFERENCE_SCHEMA['folders']['label'])
        readme = (ROOT/'README.md').read_text()
        self.assertIn('["/home/user/Pictures"]', readme)
        self.assertNotIn('one absolute path per line', readme)
        self.assertNotIn('one path per line', self.b.PREFERENCE_SCHEMA['folders']['label'])


    def test_legacy_large_preferences_preserve_confirmed_action_and_usable_reads(self):
        # Seed an old valid store directly: new writes have a smaller aggregate budget.
        self.image(); self.wallpaper_setters(); selected = self.b.catalog()['rows'][0]['id']; verb = 'apply'
        prefs = self.b.preferences()['preferences']
        prefs['collections'] = {'g'+str(g): [str(n).zfill(4)+'x'*252 for n in range(512)] for g in range(9)}
        base = Path(os.environ.get('XDG_CONFIG_HOME', str(self.home/'.config')))
        store = base/'omarchy/lego/wallpaper-browser/preferences.json'
        store.parent.mkdir(parents=True, exist_ok=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(prefs, ensure_ascii=False, separators=(',', ':')).encode()
        self.assertGreater(len(raw), 1048576)
        store.write_bytes(raw); store.chmod(0o600)
        result = subprocess.run(['/usr/bin/python3', str(BACKEND), 'action', '--id', selected, '--verb', verb, '--apply'],  capture_output=True, timeout=12)
        value = json.loads(result.stdout)
        self.assertTrue(self.b.current_background())
        self.assertEqual(result.returncode, 0, value)
        self.assertTrue(value['ok'], value)
        self.assertIn('warning', value['message'].lower())
        self.assertLessEqual(len(result.stdout), 1048576)
        self.assertNotIn('preferences', value)
        for command in ('preferences',):
            result = subprocess.run(['/usr/bin/python3', str(BACKEND), command],  capture_output=True, timeout=12)
            value = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, value)
            self.assertNotIn('preferences', value)
            self.assertIn('warning', value['message'].lower())
            self.assertLessEqual(len(result.stdout), 1048576)
            if command == 'catalog': self.assertIn(selected, [row['id'] for row in value['rows']])
        reset = subprocess.run(['/usr/bin/python3', str(BACKEND), 'preferences', '--apply', '--payload', '{"reset":true}'],  capture_output=True, timeout=8)
        self.assertEqual(reset.returncode, 0, reset.stdout)
        self.assertEqual(json.loads(reset.stdout)['preferences']['collections'], {})


    def test_restore_last_uses_snapshots_after_library_sources_disappear(self):
        old=self.image('old.png',(0,255,0));new=self.image();expected=old.read_bytes()
        self.current_background(old);self.wallpaper_setters();selected='wall:'+self.b.token(new)
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        old.unlink();new.unlink();value=self.b.catalog()
        self.assertEqual(value['rows'],[]);self.assertIn('restore',value['actions'])
        self.assertFalse(self.b.action('wall:unrelated','restore',True)['ok'])
        result=self.b.action('restore:last','restore',True);self.assertTrue(result['ok'],result)
        self.assertEqual(Path(self.b.current_background()).read_bytes(),expected)

    def test_disappearing_selected_source_still_refuses_before_provider(self):
        source=self.image();selected='wall:'+self.b.token(source);self.wallpaper_setters();actual=self.b.image_files
        def race():
            files=actual();source.unlink();return files
        with patch.object(self.b,'image_files',race),patch.object(self.b,'run',wraps=self.b.run) as provider:
            result=self.b.action(selected,'apply',True)
        self.assertFalse(result['ok']);provider.assert_not_called()
        self.assertEqual(self.b.journal(),{})

    def test_apply_refuses_current_candidate_that_disappears_or_becomes_unsafe(self):
        old=self.image('aaa.png',(0,255,0));wanted=self.image('zzz.png',(0,0,255));self.wallpaper_setters()
        selected='wall:'+self.b.token(wanted);self.current_background(old)
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        receipt_path=self.b.state_path()/'restore.json';receipt=receipt_path.read_bytes();actual=self.b.image_files
        for replacement in ('missing','symlink','fifo','oversized'):
            with self.subTest(replacement=replacement):
                if old.exists() or old.is_symlink():old.unlink()
                old.write_bytes(png((0,255,0)));self.current_background(old)
                with self.b.State() as state:state.put('restore.json',receipt)
                receipt_inode=receipt_path.stat().st_ino
                def race():
                    files=actual();self.assertIn(old,files);self.assertIn(wanted,files);old.unlink()
                    if replacement=='symlink':old.symlink_to(wanted)
                    if replacement=='fifo':os.mkfifo(old)
                    if replacement=='oversized':
                        with old.open('wb') as f:f.truncate(self.b.MAX_IMAGE+1)
                    return files
                with patch.object(self.b,'image_files',race),patch.object(self.b,'run',wraps=self.b.run) as provider:
                    result=self.b.action(selected,'apply',True)
                self.assertFalse(result['ok'],result);provider.assert_not_called()
                self.assertEqual(self.b.current_background(),str(old))
                self.assertEqual(receipt_path.read_bytes(),receipt)
                self.assertEqual(receipt_path.stat().st_ino,receipt_inode)

    def test_current_snapshot_failure_preserves_receipt_before_any_provider(self):
        old=self.image('old.png',(0,255,0));wanted=self.image('wanted.png');self.wallpaper_setters()
        selected='wall:'+self.b.token(wanted);self.current_background(old)
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        receipt_path=self.b.state_path()/'restore.json';receipt=receipt_path.read_bytes();actual=self.b.snapshot
        for replacement in ('missing','symlink','fifo','oversized'):
            with self.subTest(replacement=replacement):
                if old.exists() or old.is_symlink():old.unlink()
                old.write_bytes(png((0,255,0)));self.current_background(old)
                receipt_inode=receipt_path.stat().st_ino
                def race(state,path):
                    target=actual(state,path)
                    if path==wanted:
                        old.unlink()
                        if replacement=='symlink':old.symlink_to(wanted)
                        if replacement=='fifo':os.mkfifo(old)
                        if replacement=='oversized':
                            with old.open('wb') as f:f.truncate(self.b.MAX_IMAGE+1)
                    return target
                with patch.object(self.b,'snapshot',race),patch.object(self.b,'run',wraps=self.b.run) as provider:
                    result=self.b.action(selected,'apply',True)
                self.assertFalse(result['ok'],result);provider.assert_not_called()
                self.assertEqual(self.b.current_background(),str(old))
                self.assertEqual(receipt_path.read_bytes(),receipt)
                self.assertEqual(receipt_path.stat().st_ino,receipt_inode)

    def test_good_current_candidate_is_frozen_before_provider(self):
        old=self.image('old.png',(0,255,0));wanted=self.image();expected=old.read_bytes()
        self.current_background(old);self.wallpaper_setters();actual=self.b.run
        def provider(argv,**kwargs):
            if argv==['omarchy-shell','shell','ping']:
                old.unlink()
                self.assertTrue(any(p.read_bytes()==expected for p in self.b.state_path().glob('image-*')))
            return actual(argv,**kwargs)
        with patch.object(self.b,'run',provider):result=self.b.action('wall:'+self.b.token(wanted),'apply',True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(Path(self.b.journal()['before']).read_bytes(),expected)
        restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
        self.assertEqual(Path(self.b.current_background()).read_bytes(),expected)

    def test_selected_current_source_races_preserve_prior_receipt(self):
        source=self.image();self.current_background(source);self.wallpaper_setters()
        self.assertTrue(self.b.action('wall:'+self.b.token(source),'apply',True)['ok'])
        receipt_path=self.b.state_path()/'restore.json';receipt=receipt_path.read_bytes()
        actual_files=self.b.image_files;actual_read=self.b.read
        for stage in ('membership','snapshot'):
            for replacement in ('missing','symlink','fifo','oversized'):
                with self.subTest(stage=stage,replacement=replacement):
                    if source.exists() or source.is_symlink():source.unlink()
                    source.write_bytes(png());self.current_background(source);selected='wall:'+self.b.token(source)
                    receipt_inode=receipt_path.stat().st_ino
                    def replace_source():
                        source.unlink()
                        if replacement=='symlink':source.symlink_to(receipt_path)
                        if replacement=='fifo':os.mkfifo(source)
                        if replacement=='oversized':
                            with source.open('wb') as f:f.truncate(self.b.MAX_IMAGE+1)
                    def files():
                        result=actual_files()
                        if stage=='membership':replace_source()
                        return result
                    def read(path,*args):
                        data=actual_read(path,*args)
                        if stage=='snapshot' and Path(path)==source:replace_source()
                        return data
                    with patch.object(self.b,'image_files',files),patch.object(self.b,'read',read),patch.object(self.b,'run',wraps=self.b.run) as provider:
                        result=self.b.action(selected,'apply',True)
                    self.assertFalse(result['ok'],result);provider.assert_not_called()
                    self.assertEqual(self.b.current_background(),str(source))
                    self.assertEqual(receipt_path.read_bytes(),receipt)
                    self.assertEqual(receipt_path.stat().st_ino,receipt_inode)

    def test_unknown_current_is_not_read_or_admitted_outside_scan_limits(self):
        wanted=self.image();self.wallpaper_setters();selected='wall:'+self.b.token(wanted);actual=self.b.opened
        outside=self.home/'outside.png';outside.write_bytes(png((0,255,0)))
        overfull=self.image('album/old.png',(0,255,0))
        for i in range(self.b.MAX_ENTRIES):(overfull.parent/str(i)).touch()
        for unknown in (None,outside,overfull):
            with self.subTest(current=unknown):
                link=self.home/'.local/state/omarchy/current/background'
                if link.is_symlink():link.unlink()
                if unknown is not None:self.current_background(unknown)
                def opened(path,*args):
                    self.assertNotIn(Path(path),(outside,overfull),'unknown/over-budget current must not be opened')
                    return actual(path,*args)
                with patch.object(self.b,'opened',opened):result=self.b.action(selected,'apply',True)
                self.assertTrue(result['ok'],result);self.assertEqual(self.b.journal()['before'],'')
                self.assertNotIn('restore',self.b.catalog()['actions'])
        self.assertEqual(outside.read_bytes(),png((0,255,0)));self.assertEqual(overfull.read_bytes(),png((0,255,0)))

    def test_catalog_skips_candidate_that_disappears_after_enumeration(self):
        unrelated=self.image('aaa.png');wanted=self.image('zzz.png');actual=self.b.image_files
        def race():
            files=actual();unrelated.unlink();return files
        with patch.object(self.b,'image_files',race):value=self.b.catalog()
        self.assertEqual([r['preview'] for r in value['rows']],[str(wanted)])

    def test_apply_skips_unrelated_candidate_that_disappears_or_becomes_unsafe(self):
        unrelated=self.image('aaa.png');wanted=self.image('zzz.png',(0,0,255));self.wallpaper_setters()
        selected='wall:'+self.b.token(wanted);actual=self.b.image_files
        for replacement in ('missing','symlink','fifo','oversized'):
            with self.subTest(replacement=replacement):
                if unrelated.exists() or unrelated.is_symlink():unrelated.unlink()
                unrelated.write_bytes(png())
                def race():
                    files=actual();unrelated.unlink()
                    if replacement=='symlink':unrelated.symlink_to(wanted)
                    if replacement=='fifo':os.mkfifo(unrelated)
                    if replacement=='oversized':
                        with unrelated.open('wb') as f:f.truncate(self.b.MAX_IMAGE+1)
                    return files
                with patch.object(self.b,'image_files',race):result=self.b.action(selected,'apply',True)
                self.assertTrue(result['ok'],result)
                self.assertEqual(Path(self.b.current_background()).read_bytes(),wanted.read_bytes())

    def test_unrelated_disappearing_image_does_not_block_restore(self):
        old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
        selected='wall:'+self.b.token(new)
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        unrelated=self.image('unrelated.png');actual=self.b.image_files
        def disappear():
            files=actual();unrelated.unlink();return files
        with patch.object(self.b,'image_files',wraps=disappear) as scan:
            result=self.b.action('restore:last','restore',True)
        self.assertTrue(result['ok'],result)
        scan.assert_not_called()
        self.assertEqual(Path(self.b.current_background()).read_bytes(),old.read_bytes())

    def test_restore_refuses_in_place_current_snapshot_edit(self):
        old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new))
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        receipt_path=self.b.state_path()/'restore.json';receipt=receipt_path.read_bytes()
        saved=self.b.journal();current=Path(saved['after']);inode=current.stat().st_ino
        edited=png((0,0,255))
        with current.open('r+b',buffering=0) as editor:
            editor.write(edited);editor.truncate();os.fsync(editor.fileno())
        self.assertEqual(current.stat().st_ino,inode)
        before={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        for restore_id in (selected,'restore:last'):
            with patch.object(self.b,'run',wraps=self.b.run) as provider:
                result=self.b.action(restore_id,'restore',True)
            self.assertFalse(result['ok'],result)
            self.assertIn('current snapshot',result['message'].lower())
            provider.assert_not_called()
            self.assertEqual(self.b.current_background(),str(current))
            self.assertEqual(current.read_bytes(),edited)
            self.assertEqual(receipt_path.read_bytes(),receipt)
            after={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
            self.assertEqual(after,before,'refused restore must preserve all fixture bytes')

    def test_snapshot_retention_is_bounded(self):
        self.b.MAX_SNAPSHOTS=2
        with self.b.State() as state:
            self.b.snapshot(state,self.image('a.png',(255,0,0)))
            self.b.snapshot(state,self.image('b.png',(0,255,0)))
            with self.assertRaises(self.b.OperationError):self.b.snapshot(state,self.image('c.png',(0,0,255)))

    def test_deeply_nested_journal_fails_closed(self):
        with self.b.State() as state:state.put('restore.json',b'['*2000+b']'*2000)
        self.assertEqual(self.b.journal(),{})
    def test_empty_catalog_is_honest(self):
        result=self.b.catalog()
        self.assertEqual(result['rows'],[])
        self.assertIn('no ',result['message'].lower())
    def test_cli_real_action_and_restore_round_trip(self):
        if self.b.PLUGIN=='theme-gallery':
            self.theme('before',True);self.theme('after',True);self.current_theme('before');self.theme_setter()
            selected,verb='theme:after','apply'
        elif self.b.PLUGIN=='wallpaper-browser':
            old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
            selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new));verb='apply'
        else:
            self.image();selected=self.b.catalog()['rows'][0]['id'];verb='export'
        def cli(v,confirm=True):
            args=['/usr/bin/python3',str(BACKEND),'action','--id',selected,'--verb',v]+(['--apply'] if confirm else [])
            r=subprocess.run(args,capture_output=True,text=True,timeout=10,cwd='/')
            self.assertNotIn('Traceback',r.stderr)
            return r,json.loads(r.stdout)
        r,value=cli(verb,False);self.assertNotEqual(r.returncode,0);self.assertFalse(value['ok'])
        r,value=cli(verb);self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
        r,value=cli('restore');self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
    def test_sandbox_has_no_live_ipc_home_or_network(self):
        self.assertFalse(Path('/srv').exists())
        self.assertEqual(sorted(p.name for p in Path('/home').iterdir()),['test'])
        self.assertFalse(Path('/run/user').exists())
        interfaces=[line.split(':')[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]]
        self.assertEqual(interfaces,['lo'])
        for key in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE','DBUS_SESSION_BUS_ADDRESS'):
            self.assertNotIn(key,os.environ)

    def test_quiet_success_without_real_ack_is_failure(self):
        p=self.image();self.wallpaper_setters()
        self.shim('omarchy-shell',"if sys.argv[1:]==['shell','ping']:print('ok')\nelse:print('Not ready to accept queries yet')\n")
        selected=self.b.catalog()['rows'][0]['id']
        result=self.b.action(selected,'apply',True)
        self.assertFalse(result['ok'],result)
        self.assertIn('acknowledged',result['message'].lower())
    def test_restoration_refuses_external_background(self):
        old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new))
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        self.current_background(old)
        self.assertFalse(self.b.action(selected,'restore',True)['ok'])
    def test_stale_replaced_image_never_reaches_setter(self):
        p=self.image();selected=self.b.catalog()['rows'][0]['id'];p.write_bytes(png((0,255,0)))
        self.wallpaper_setters()
        self.assertFalse(self.b.action(selected,'apply',True)['ok'])
        self.assertFalse((self.home/'bg-argv').exists())
    def test_snapshot_race_cannot_apply_replaced_source(self):
        p=self.image();selected=self.b.catalog()['rows'][0]['id'];self.wallpaper_setters()
        actual=self.b.read
        def race(path,*args):
            data=actual(path,*args)
            if Path(path)==p:p.write_bytes(png((0,255,0)))
            return data
        with patch.object(self.b,'read',race):result=self.b.action(selected,'apply',True)
        self.assertFalse(result['ok']);self.assertFalse((self.home/'bg-argv').exists())
    def test_missing_shell_preflight_does_not_run_setter(self):
        p=self.image();selected=self.b.catalog()['rows'][0]['id'];self.wallpaper_setters()
        self.shim('omarchy-shell','sys.exit(1)\n')
        self.assertFalse(self.b.action(selected,'apply',True)['ok'])
        self.assertFalse((self.home/'bg-argv').exists())
    def test_setter_noop_is_not_persistent_confirmation(self):
        self.image();selected=self.b.catalog()['rows'][0]['id'];self.wallpaper_setters()
        self.shim('omarchy-theme-bg-set','sys.exit(0)\n')
        self.assertFalse(self.b.action(selected,'apply',True)['ok'])
    def test_one_level_collections_and_theme_backgrounds(self):
        self.image('album/a.png');self.image('album/deeper/no.png')
        p=self.theme();(p/'backgrounds').mkdir();(p/'backgrounds/wall.png').write_bytes(png())
        rows=self.b.catalog()['rows']
        self.assertEqual(len(rows),2)
        self.assertTrue(any('album/a.png' in r['preview'] for r in rows))
    def test_full_membership_beyond_display_cap(self):
        for i in range(270):self.image('%03d.png'%i)
        files=self.b.image_files();self.assertEqual(len(self.b.catalog()['rows']),256)
        selected='wall:'+self.b.token(files[-1]);self.wallpaper_setters()
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
    def test_current_selection_is_identified(self):
        self.image();selected=self.b.catalog()['rows'][0]['id'];self.wallpaper_setters()
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        row=next(r for r in self.b.catalog()['rows'] if r['id']==selected)
        self.assertIn('current',row['detail'].lower())
    def test_corrupt_snapshot_prevents_restore(self):
        old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new))
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        Path(self.b.journal()['before']).write_bytes(png())
        self.assertFalse(self.b.action(selected,'restore',True)['ok'])

    def test_catalog_is_read_only(self):
        self.image();self.theme()
        before={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.b.catalog()
        after={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(after,before)
    def test_catalog_rejects_symlink_ancestor(self):
        outside=self.home/'external';outside.mkdir();(outside/'escape.png').write_bytes(png())
        (self.home/'Pictures').symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.b.image_files(),[])
    def test_fifo_and_oversized_image_are_not_members(self):
        p=self.image();root=p.parent
        os.mkfifo(root/'pipe.png')
        with (root/'large.png').open('wb') as stream: stream.truncate(self.b.MAX_IMAGE+1)
        self.assertEqual(self.b.image_files(),[p])
    def test_ids_change_on_replacement_but_not_refresh(self):
        p=self.image();first=self.b.token(p)
        self.assertEqual(self.b.token(p),first)
        replacement=p.with_name('replace');replacement.write_bytes(png((0,255,0)));replacement.replace(p)
        self.assertNotEqual(self.b.token(p),first)
    def test_racing_file_read_fails_closed(self):
        p=self.image();actual=self.b.opened
        import contextlib
        @contextlib.contextmanager
        def racing(path,limit):
            with actual(path,limit) as (stream,info):
                class Proxy:
                    def read(inner,n):
                        data=stream.read(n);p.write_bytes(png((0,255,0)));return data
                    def fileno(inner):return stream.fileno()
                yield Proxy(),info
        with patch.object(self.b,'opened',racing):
            with self.assertRaises(ValueError):self.b.read(p,self.b.MAX_IMAGE)
    def test_subprocess_output_is_bounded(self):
        with self.assertRaises(self.b.OperationError):
            self.b.run(['/usr/bin/python3','-c','import sys;sys.stdout.write("x"*1000000)'],timeout=1)
    def test_action_lock_is_exclusive(self):
        with self.b.State():
            with self.assertRaises(OSError):
                with self.b.State():pass
    def test_state_symlink_is_rejected(self):
        dest=self.home/'untouched';dest.mkdir()
        state=self.b.state_path();state.parent.mkdir(parents=True,exist_ok=True);state.symlink_to(dest,target_is_directory=True)
        result=self.b.action('unknown','restore',True)
        self.assertFalse(result['ok']);self.assertEqual(list(dest.iterdir()),[])
    def test_literal_invalid_ids_cannot_execute(self):
        for value in ('../../escape','$(touch injected)',';touch injected','--help','bad\nname','x'*200):
            self.assertFalse(self.b.action(value,'apply',True)['ok'])
            self.assertFalse(self.b.action(value,'restore',True)['ok'])
        self.assertFalse((self.home/'injected').exists())
    def test_scan_budget_does_not_read_overfull_directory(self):
        p=self.image()
        for i in range(self.b.MAX_ENTRIES): (p.parent/str(i)).touch()
        self.assertEqual(self.b.image_files(),[])
    def test_png_dimension_bomb_is_rejected(self):
        p=self.image();data=bytearray(p.read_bytes());data[16:24]=struct.pack('!II',100000,100000);p.write_bytes(data)
        self.assertFalse(self.b.valid_image(p))

    def test_timeout_kills_descendant_after_parent_exits(self):
        script=self.home/'fork.py'
        script.write_text("import os,time,signal\nfrom pathlib import Path\npid=os.fork()\nif pid: Path(os.environ['HOME']+'/child.pid').write_text(str(pid));os._exit(0)\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(30)\n")
        with self.assertRaises(self.b.OperationError): self.b.run(['/usr/bin/python3',str(script)],timeout=0.15)
        pid=int((self.home/'child.pid').read_text())
        import time,signal
        time.sleep(0.05)
        status=Path('/proc')/str(pid)/'stat'
        state=status.read_text().split()[2] if status.exists() else 'gone'
        if state not in ('gone','Z'):
            os.kill(pid,signal.SIGKILL)
        self.assertIn(state,('gone','Z'),'provider descendant survived timeout')

    def shim(self,name,body):
        p=self.home/'bin'/name
        p.write_text('#!/usr/bin/python3\nimport os,json,sys\nfrom pathlib import Path\nh=Path(os.environ["HOME"])\n'+body)
        p.chmod(0o755)

    def wallpaper_setters(self):
        self.shim('omarchy-theme-bg-set',"p=h/'.local/state/omarchy/current';p.mkdir(parents=True,exist_ok=True)\nx=p/'background'\nif x.is_symlink(): x.unlink()\nx.symlink_to(sys.argv[1])\n(h/'bg-argv').write_text(json.dumps(sys.argv[1:]))\n")
        self.shim('omarchy-shell',"with (h/'ipc-calls').open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nif sys.argv[1:]==['shell','ping']:print('ok')\nelif sys.argv[1:3]==['background','set']:pass\nelse:sys.exit(1)\n")
    def current_background(self,p):
        link=self.home/'.local/state/omarchy/current/background';link.parent.mkdir(parents=True,exist_ok=True)
        if link.is_symlink():link.unlink()
        link.symlink_to(p)
    def test_apply_frozen_image_and_confirmed_restore(self):
        old=self.image('old.png',(0,255,0));new=self.image('-sp ace;$(x).png')
        self.current_background(old);self.wallpaper_setters()
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new))
        self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        used=Path(json.loads((self.home/'bg-argv').read_text())[0])
        self.assertNotEqual(used,new);self.assertEqual(used.read_bytes(),new.read_bytes())
        calls=[json.loads(x) for x in (self.home/'ipc-calls').read_text().splitlines()]
        self.assertEqual(calls[-1],['background','set',str(used)])
        self.assertIn('restore',self.b.catalog()['actions'])
        self.assertTrue(self.b.action(selected,'restore',True)['ok'])
        link=self.home/'.local/state/omarchy/current/background'
        self.assertEqual(Path(os.readlink(link)).read_bytes(),old.read_bytes())

    def test_cli_catalog_and_explicit_action_gate(self):
        self.image()
        self.theme()
        def cli(*args):
            return subprocess.run(['/usr/bin/python3',str(BACKEND),*args],capture_output=True,text=True,timeout=8,cwd='/')
        r=cli('catalog');self.assertEqual(r.returncode,0)
        value=json.loads(r.stdout);self.assertIn('rows',value);self.assertIn('actions',value)
        self.assertLess(len(r.stdout.encode()),1048576)
        for args in [('action','--id','../../no','--verb','apply'),('action','--id','bad','--verb','exec','--apply')]:
            r=cli(*args);self.assertNotEqual(r.returncode,0)
            value=json.loads(r.stdout);self.assertFalse(value['ok']);self.assertNotIn('Traceback',r.stderr)

    def test_catalog_rejects_unsafe_members(self):
        good=self.image()
        root=good.parent
        (root/'link.png').symlink_to(good)
        (root/'bad.png').write_bytes(b'not an image')
        (root/'bad.svg').write_text('<svg/>')
        (root/'line\nname.png').write_bytes(png())
        outside=self.home/'outside';outside.mkdir();(outside/'escape.png').write_bytes(png())
        (root/'collection').symlink_to(outside,target_is_directory=True)
        rows=self.b.catalog()['rows']
        self.assertEqual({r['preview'] for r in rows},{str(good)})

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'XDG_CONFIG_HOME':str(self.home/'preferences-config'),'PATH':str(self.home/'bin')+':/usr/bin'},clear=True)
        self.env.start()
        (self.home/'bin').mkdir()
        spec=importlib.util.spec_from_file_location('backend',BACKEND)
        self.b=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.b)
        self.b.STOCK=self.home/'stock'
        self.b.STOCK.mkdir()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def theme(self,name='test-theme',user=False):
        root=self.home/'.config/omarchy/themes' if user else self.b.STOCK
        p=root/name;p.mkdir(parents=True,exist_ok=True)
        (p/'colors.toml').write_text('accent = "#123456"\n')
        return p
    def image(self,name='red.png',rgb=(255,0,0)):
        p=self.home/'Pictures/Wallpapers'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(png(rgb));return p

    def test_catalog_local_wallpaper(self):
        p=self.image('sp ace;$(x).png')
        self.assertTrue(callable(getattr(self.b,'catalog',None)), 'catalog behavior missing')
        rows=self.b.catalog()['rows']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['preview'],str(p))
        self.assertTrue(rows[0]['id'].startswith('wall:'))
