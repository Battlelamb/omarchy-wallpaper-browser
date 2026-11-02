# SPDX-License-Identifier: GPL-3.0-only
"""Independent bounded local adapter; no daemon, network or shell interpolation."""
import argparse
import contextlib
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parent))

PREFERENCE_SCHEMA = {
    'density': {'type':'enum','default':'comfortable','choices':['comfortable','compact'],'label':'Density'},
    'layout': {'type':'enum','default':'grid','choices':['grid','list'],'label':'Layout'},
    'sortOrder': {'type':'enum','default':'default','choices':['default','name','favorites-first'],'label':'Sort order'},
    'fileType': {'type':'enum','default':'all','choices':['all','png','jpeg','webp','bmp'],'label':'File type'},
    'aspect': {'type':'enum','default':'all','choices':['all','landscape','portrait','square','ultrawide','unknown'],'label':'Aspect ratio'},
    'favoritesOnly': {'type':'boolean','default':False,'label':'Favorites only'},
    'folders': {'type':'string-list','default':[],'maxLength':4096,'label':'Saved local folders (JSON array)'},
    'collection': {'type':'string','default':'','maxLength':256,'label':'Collection (empty = all)'},
}

def prefs_module():
    import preferences
    return preferences

def payload_object(raw):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>16384: raise ValueError('Payload exceeds 16384 UTF-8 bytes')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('Duplicate payload key')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite payload number')))
    if not isinstance(value,dict): raise ValueError('Payload must be an object')
    return value

def preferences(apply=False,payload=None):
    if payload is None:
        if apply: raise ValueError('Preference writes require a payload')
        prefs=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)
    else:
        if not apply: raise ValueError('Explicit --apply is required')
        if payload=={'reset':True} and type(payload['reset']) is bool: prefs=prefs_module().reset(PLUGIN,PREFERENCE_SCHEMA)
        else:
            if isinstance(payload,dict) and isinstance(payload.get('options'),dict) and 'folders' in payload['options']:
                folders=payload['options']['folders']
                if not isinstance(folders,list) or len(folders)>32: raise ValueError('At most 32 local folders')
                for folder in folders: saved_folder(folder)
            prefs=prefs_module().change(PLUGIN,PREFERENCE_SCHEMA,payload)
    return {'ok':True,'preferences':prefs,'settingsSchema':PREFERENCE_SCHEMA,'message':'Preferences saved.' if apply else 'Saved preferences.'}

STOCK = Path('/usr/share/omarchy/themes')
MAX_ROWS = 256
MAX_FILES = 512
MAX_ENTRIES = 1024
MAX_IMAGE = 32 * 1024 * 1024
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
HEX = re.compile(r'#[0-9a-fA-F]{6}\Z')

def home():
    p = Path(os.environ['HOME'])
    if not p.is_absolute(): raise ValueError('HOME must be absolute')
    return p

def text(value, limit=160):
    return isinstance(value,str) and 0 < len(value) <= limit and not any(ord(c)<32 or ord(c)==127 or 0xd800<=ord(c)<=0xdfff for c in value)

def directory(path, create=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('Unsafe local path')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part,0o700,dir_fd=fd)
                except FileExistsError: pass
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        return fd
    except BaseException:
        os.close(fd);raise

def names(path):
    try:
        fd=directory(path)
        try:
            with os.scandir(fd) as entries:
                out=[e.name for e in itertools.islice(entries,MAX_ENTRIES+1)]
            if len(out)>MAX_ENTRIES: return []
            return sorted(n for n in out if text(n) and not n.startswith('.'))
        finally: os.close(fd)
    except OSError: return []

def is_directory(path):
    try: fd=directory(path);os.close(fd);return True
    except OSError: return False

@contextlib.contextmanager
def opened(path,limit=MAX_IMAGE):
    fd=directory(Path(path).parent)
    try:
        filefd=os.open(Path(path).name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info=os.fstat(filefd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>limit: raise ValueError('File is not regular or exceeds size limit')
            with os.fdopen(filefd,'rb',closefd=False) as stream: yield stream,info
        finally: os.close(filefd)
    finally: os.close(fd)

def fingerprint(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def read(path,limit=65536):
    with opened(path,limit) as (stream,before):
        data=stream.read(limit+1)
        if len(data)>limit or fingerprint(os.fstat(stream.fileno()))!=fingerprint(before): raise ValueError('File changed while reading')
        return data

def optional_read(path,limit=65536):
    try: return read(path,limit)
    except (OSError,ValueError): return b''

def themes():
    result={}
    user=home()/'.config/omarchy/themes'
    for root in (STOCK,user):
        for name in names(root):
            if SLUG.fullmatch(name) and is_directory(root/name): result.setdefault(name,[]).append(root/name)
    # A user symlink would still be followed by the stock setter: block its stock slug too.
    for name in list(result):
        p=user/name
        if os.path.lexists(p) and not is_directory(p): del result[name]
    return dict(sorted(result.items()))

def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24:
        width,height=struct.unpack('!II',data[16:24])
        if width and height and width<=16384 and height<=16384 and width*height<=64000000: return 'PNG'
    if data.startswith(b'\xff\xd8\xff'): return 'JPEG'
    if len(data)>12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'WEBP'
    if data.startswith(b'BM'): return 'BMP'
    raise ValueError('Unsupported or oversized image header')

def valid_image(path):
    if not text(str(path),4096) or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.bmp'): return False
    try:
        with opened(path) as (stream,info): image_type(stream.read(64))
        return True
    except (OSError,ValueError): return False

def saved_folder(value):
    if not text(value,4096) or not Path(value).is_absolute(): raise ValueError('Folder must be an absolute local path')
    fd=directory(value)
    try:
        if os.fstat(fd).st_uid!=os.getuid() or Path(value)==Path('/'): raise ValueError('Folder must be owned by the current user')
    finally: os.close(fd)
    return Path(value)

def image_roots():
    roots=[]
    for base in (home()/'Pictures/Wallpapers',home()/'.config/omarchy/backgrounds'):
        roots.append(base)
        roots.extend(base/name for name in names(base) if is_directory(base/name))
    roots += [p/'backgrounds' for paths in themes().values() for p in paths]
    roots.append(home()/'.local/state/omarchy/current/theme/backgrounds')
    for value in prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)['options']['folders']:
        try: base=saved_folder(value)
        except (OSError,ValueError): continue
        roots.append(base)
        roots.extend(base/name for name in names(base) if is_directory(base/name))
    return roots[:MAX_FILES]

def image_files():
    result=[];seen=set()
    for root in image_roots():
        for name in names(root):
            p=root/name
            if p not in seen and valid_image(p):
                result.append(p);seen.add(p)
                if len(result)>=MAX_FILES: return result
    return result

def token(p):
    with opened(p) as (_,info): stamp=fingerprint(info)
    return hashlib.sha256((str(p)+repr(stamp)).encode()).hexdigest()[:32]

def dimensions(path):
    with opened(path) as (stream,before):
        data=stream.read(65536)
        if fingerprint(os.fstat(stream.fileno()))!=fingerprint(before): raise ValueError('Image changed while reading dimensions')
    width=height=0
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24: width,height=struct.unpack('!II',data[16:24])
    elif data.startswith(b'BM') and len(data)>=26: width,height=struct.unpack('<ii',data[18:26]);height=abs(height)
    elif data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        if data[12:16]==b'VP8X' and len(data)>=30: width=1+int.from_bytes(data[24:27],'little');height=1+int.from_bytes(data[27:30],'little')
        elif data[12:16]==b'VP8L' and len(data)>=25 and data[20]==47:
            bits=int.from_bytes(data[21:25],'little');width=(bits&16383)+1;height=((bits>>14)&16383)+1
        elif data[12:16]==b'VP8 ' and len(data)>=30 and data[23:26]==b'\x9d\x01\x2a': width,height=(n&16383 for n in struct.unpack('<HH',data[26:30]))
    elif data.startswith(b'\xff\xd8'):
        offset=2
        while offset+4<=len(data):
            if data[offset]!=255: break
            marker=data[offset+1];offset+=2
            if marker==255: offset-=1;continue
            if marker in (0xD8,0xD9,0x01) or 0xD0<=marker<=0xD7: continue
            size=int.from_bytes(data[offset:offset+2],'big')
            if size<2 or offset+size>len(data): break
            if marker in (0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF) and size>=7:
                height,width=struct.unpack('!HH',data[offset+3:offset+7]);break
            offset+=size
    if not (0<width<=16384 and 0<height<=16384 and width*height<=64000000): return 0,0,'unknown'
    category='square' if width==height else 'ultrawide' if width/height>=2 else 'landscape' if width>height else 'portrait'
    return width,height,category

def catalog():
    saved=journal();current=current_background()
    prefs=prefs_module().read(PLUGIN,PREFERENCE_SCHEMA)
    favorites=set(prefs['favorites'])
    rows=[]
    for p in image_files():
        try:
            identity='wall:'+token(p);width,height,category=dimensions(p)
            with opened(p) as (stream,before):
                kind=image_type(stream.read(64)).lower()
                if fingerprint(os.fstat(stream.fileno()))!=fingerprint(before): raise ValueError('Image changed while reading type')
        except (OSError,ValueError): continue
        active=(str(p)==current or (saved.get('selected')==identity and saved.get('after')==current))
        favorite=identity in favorites
        groups=[name for name,ids in prefs['collections'].items() if identity in ids]
        options=prefs['options']
        if (options['fileType']!='all' and options['fileType']!=kind) or (options['favoritesOnly'] and not favorite) or (options['aspect']!='all' and options['aspect']!=category) or (options['collection'] and options['collection'] not in groups): continue
        detail='Local wallpaper · '+(f'{width} × {height} · {width/height:.2f}:1' if height else 'dimensions unknown')+(' · Current selection' if active else '')
        rows.append({'id':identity,'label':p.name,'detail':detail,'preview':str(p),'fileType':kind,'favorite':favorite,'category':category,'collection':groups[0] if groups else p.parent.name})
    # Match Controller's lowercase UTF-16 ordinal order; stable sorts retain input ties.
    if prefs['options']['sortOrder']=='name': rows.sort(key=lambda row:row['label'].lower().encode('utf-16-be'))
    elif prefs['options']['sortOrder']=='favorites-first': rows.sort(key=lambda row:not row['favorite'])
    return {'rows':rows[:MAX_ROWS],'preferences':prefs,'settingsSchema':PREFERENCE_SCHEMA,'message':('Local still images; explicit apply only. Showing at most 256 of the bounded 512-file membership.' if rows else 'No safe local wallpapers match these filters/roots.'),'actions':['apply','favorite','shuffle']+(['restore'] if remembered_image(journal().get('before')) else []),'actionLabel':'Apply'}


class OperationError(ValueError): pass

def run(argv, payload=None, timeout=12, env=None):
    """Bound both collection and process group lifetime, including pipe-holding children."""
    source=None
    if payload is not None:
        source=os.fdopen(os.memfd_create('palette-input',os.MFD_CLOEXEC),'w+b')
        source.write(payload);source.seek(0)
    try:
        proc=subprocess.Popen(argv,stdin=source if source else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env=env)
    finally:
        if source: source.close()
    complete=False
    output=[bytearray(),bytearray()];deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            for index,pipe in enumerate((proc.stdout,proc.stderr)):
                os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,index)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise OperationError('Provider timed out; state may be partially changed. Refresh before retrying.')
                for key,_ in selector.select(min(remaining,0.1)):
                    block=os.read(key.fd,8192)
                    if not block: selector.unregister(key.fileobj);continue
                    output[key.data].extend(block)
                    if len(output[key.data])>65536: raise OperationError('Provider output exceeded limit; state may be partially changed.')
            try: code=proc.wait(timeout=max(0.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: raise OperationError('Provider timed out; state may be partially changed.') from None
        complete=True
        return code,bytes(output[0]),bytes(output[1])
    finally:
        # Do not leave descendants holding an interrupted operation alive.
        if not complete or proc.poll() is None:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        for pipe in (proc.stdout,proc.stderr): pipe.close()
        proc.wait()

def state_path(): return home()/'.local/state'/('omarchy-'+PLUGIN)

class State:
    def __enter__(self):
        self.fd=directory(state_path(),True)
        try:
            self.lock=os.open('lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=self.fd)
            if not stat.S_ISREG(os.fstat(self.lock).st_mode) or os.fstat(self.lock).st_nlink!=1: raise OperationError('Unsafe action lock')
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            if hasattr(self,'lock'): os.close(self.lock)
            os.close(self.fd);raise
        return self
    def __exit__(self,*_):
        os.close(self.lock);os.close(self.fd)
    def put(self,name,data):
        temp='.write-'+os.urandom(12).hex()
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        try:
            with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,name,src_dir_fd=self.fd,dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try: os.unlink(temp,dir_fd=self.fd)
            except FileNotFoundError: pass
    def save(self,value): self.put('restore.json',json.dumps(value).encode())
    def clear(self):
        try: os.unlink('restore.json',dir_fd=self.fd)
        except FileNotFoundError: pass

def journal():
    try:
        value=json.loads(read(state_path()/'restore.json',8192))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

PLUGIN='wallpaper-browser'
MAX_SNAPSHOTS=16
MAX_SNAPSHOT_BYTES=256*1024*1024

def current_background():
    try:
        fd=directory(home()/'.local/state/omarchy/current')
        try: value=os.readlink('background',dir_fd=fd)
        finally: os.close(fd)
        return value if text(value,4096) and Path(value).is_absolute() and '..' not in Path(value).parts else ''
    except OSError: return ''

def snapshot(state,p):
    data=read(p,MAX_IMAGE);coder=image_type(data)
    name='image-'+hashlib.sha256(data).hexdigest()+{'PNG':'.png','JPEG':'.jpg','WEBP':'.webp','BMP':'.bmp'}[coder]
    target=state_path()/name
    if os.path.lexists(target):
        if read(target,MAX_IMAGE)!=data: raise OperationError('Snapshot collision or externally modified snapshot')
    else:
        existing=[state_path()/n for n in names(state_path()) if n.startswith('image-')]
        total=0
        for item in existing:
            with opened(item) as (_,info): total+=info.st_size
        if len(existing)>=MAX_SNAPSHOTS or total+len(data)>MAX_SNAPSHOT_BYTES: raise OperationError('Wallpaper snapshot quota reached (16 files / 256 MiB). Prune only unused snapshots manually; current and restore files must remain.')
        state.put(name,data)
    return str(target)

def remembered_image(value):
    if not isinstance(value,str): return False
    p=Path(value)
    if p.parent!=state_path() or not re.fullmatch(r'image-[0-9a-f]{64}\.(png|jpg|webp|bmp)',p.name): return False
    try: return hashlib.sha256(read(p,MAX_IMAGE)).hexdigest()==p.name[6:70] and valid_image(p)
    except (OSError,ValueError): return False

def perform(selected,verb):
    with State() as state:
        saved=journal();before=current_background()
        if verb=='restore':
            if not saved or selected not in (saved.get('selected'),'restore:last'): raise OperationError('No matching restore receipt')
            if before!=saved.get('after'): raise OperationError('Wallpaper changed externally; restore refused')
            if not remembered_image(before): raise OperationError('Current snapshot is missing or changed; restore refused')
            target=saved.get('before')
            if not remembered_image(target): raise OperationError('Previous snapshot is missing or changed; restore refused')
        elif verb=='apply':
            members={}
            for candidate in image_files():
                try: identity='wall:'+token(candidate)
                except (OSError,ValueError):
                    if str(candidate)==before: raise OperationError('Current wallpaper changed or disappeared; nothing applied')
                    continue
                members[identity]=candidate
            p=members.get(selected)
            if p is None: raise OperationError('Image changed or disappeared; refresh')
            # Freeze bytes before handing a path to an external program; never race an external library file.
            target=snapshot(state,p)
            if selected!='wall:'+token(p): raise OperationError('Image changed during snapshot; refresh')
            old=''
            if before in {str(p) for p in members.values()} or remembered_image(before): old=snapshot(state,Path(before))
        else: raise OperationError('Unsupported action')
        code,out,err=run(['omarchy-shell','shell','ping'],timeout=3)
        if code or out.strip()!=b'ok' or err: raise OperationError('Omarchy shell is unavailable; nothing applied')
        if verb=='apply':
            state.save({'selected':selected,'before':old,'after':target,'pending':True})
        code,out,err=run(['omarchy-theme-bg-set',target],timeout=5)
        if code or err or current_background()!=target: raise OperationError('Wallpaper setter did not confirm persistent state; partial changes may remain')
        # The stock setter is quiet best-effort. A non-quiet call must actually dispatch the method.
        code,out,err=run(['omarchy-shell','background','set',target],timeout=3)
        if code or err or out.strip():
            raise OperationError('Wallpaper state changed, but shell dispatch was not acknowledged; not reporting success')
        if not remembered_image(target) or current_background()!=target: raise OperationError('Wallpaper changed during acknowledgement; refresh')
        if verb=='restore': state.clear()
        else:
            saved=journal();saved['pending']=False;state.save(saved)
        return {'ok':True,'message':'Wallpaper persisted and non-quiet shell setter acknowledged. Pixel presentation is not exposed by stock IPC. Existing rotation remains unchanged.'}

def action(selected,verb,apply=False,payload=None):
    if not apply: return {'ok':False,'message':'Explicit --apply is required; nothing changed.'}
    if payload is not None: return {'ok':False,'message':'This action does not accept a payload'}
    if not text(selected,160) or verb not in ('apply','restore','export','favorite','shuffle'): return {'ok':False,'message':'Invalid selection or action'}
    try:
        if verb in ('favorite','shuffle'):
            if selected not in {'wall:'+token(p) for p in image_files()}: raise OperationError('Image changed or disappeared; refresh')
        if verb=='shuffle':
            rows=catalog()['rows']
            choices=[row for row in rows if row['id']!=selected] or rows
            if not choices: raise OperationError('No wallpaper matches the saved filters')
            row=secrets.choice(choices)
            return {'ok':True,'selectedId':row['id'],'message':'Selected '+row['label']+'; not applied. Press Apply explicitly.'}
        if verb=='favorite':
            return {'ok':True,'message':'Favorite updated.','preferences':prefs_module().toggle(PLUGIN,PREFERENCE_SCHEMA,selected)}
        result=perform(selected,verb)
        if verb=='apply':
            try: result['preferences']=prefs_module().remember(PLUGIN,PREFERENCE_SCHEMA,selected)
            except (OSError,ValueError): result['message']+=' Warning: recent selection could not be saved.'
        return result
    except OperationError as error: return {'ok':False,'message':str(error)[:600]}
    except (OSError,ValueError,KeyError,subprocess.SubprocessError): return {'ok':False,'message':'Local operation failed; check provider, permissions and safe roots. Partial state may remain; refresh.'}


class Parser(argparse.ArgumentParser):
    def error(self,message): raise ValueError('Invalid command arguments; use catalog or action --id ID --verb VERB --apply')

def main():
    try:
        parser=Parser(add_help=False,allow_abbrev=False)
        parser.add_argument('command',choices=('catalog','action','preferences'))
        parser.add_argument('--id')
        parser.add_argument('--verb')
        parser.add_argument('--payload')
        parser.add_argument('--apply',action='store_true')
        args=parser.parse_args()
        if args.command=='catalog':
            if args.id is not None or args.verb is not None or args.apply or args.payload is not None: raise ValueError('Catalog takes no options')
            value=catalog()
        elif args.command=='preferences':
            if args.id is not None or args.verb is not None: raise ValueError('Unexpected preference options')
            value=preferences(args.apply,payload_object(args.payload) if args.payload is not None else None)
        else:
            if args.id is None or args.verb is None: raise ValueError('Action needs --id and --verb')
            value=action(args.id,args.verb,args.apply,payload_object(args.payload) if args.payload is not None else None)
        raw=prefs_module().encode_reply(value)
        print(raw)
        return 0 if args.command=='catalog' or value.get('ok') is True else 1
    except (OSError,ValueError,KeyError,RecursionError,subprocess.SubprocessError):
        print(prefs_module().encode_reply({'ok':False,'message':'Local operation failed; check provider availability, file permissions and refresh.'}))
        return 1

if __name__=='__main__': sys.exit(main())
