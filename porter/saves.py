# The save manager over adb (python -m porter saves …): the game's save between the device and the PC. The app keeps its save in internal storage, which adb cannot
# read in a release build, so the app copies it out itself (nxnative/dm/android/saves_android.c) into its files folder's saves/, one folder per export, the save's
# files as they are (<name>.device beside it for a device save): from the pause menu's Export save, or at its next start when saves/export.txt asks. porter asks
# that way: it stops the app, writes the request, starts the app, which exports and ends at once (the game does not start), and reads saves/result.txt.
# An import pushes a folder (a console's save as its save tools copy it, or an export) into saves/ and names it in saves/import.txt; the app swaps it in at its next
# start, before the game mounts its save, after exporting the save it replaces (<stamp>-before-import). porter starts the app for it and reads the outcome.
import re, secrets, shlex, shutil, tempfile, time, zipfile
from pathlib import Path

from porter import datapack
from porter.adb import Adb, AdbError
from porter.upstream import BuildError, say

PREFIX = 'org.nx2a.'   # the packages ports install as (profile key package)
NAME_OK = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._ +()-]{0,62}$')


def saves_folder(package): return f'{datapack.files_folder(package)}/saves'


def apps(adb):
    """The ported games installed on the device: [package]"""
    out = adb.shell(f'pm list packages {PREFIX}', check=False)
    return sorted(ln[len('package:'):].strip() for ln in out.splitlines() if ln.startswith('package:' + PREFIX))


def pick_app(adb, package=''):
    have = apps(adb)
    if package:
        if package not in have: raise BuildError(f'{package} 未安装在设备上' + (f'（设备上已移植的游戏：{", ".join(have)}）' if have else ''))
        return package
    if len(have) == 1: return have[0]
    raise BuildError('设备上没有已移植的游戏' if not have else f'设备上有多个已移植的游戏：请用 --package 指定一个（{", ".join(have)}）')


def listing(adb, package):
    """The save folders in the app's saves/, newest first: [{name, files, bytes, time, device}]"""
    root, rows = saves_folder(package), {}
    for rel, sz in adb.remote_sizes(root).items():
        top = rel.split('/', 1)[0]
        if '/' not in rel or top.startswith('.'): continue   # result.txt and the requests; hidden: an export still being copied
        r = rows.setdefault(top.removesuffix('.device'), {'files': 0, 'bytes': 0, 'device': False})
        r['files'] += 1; r['bytes'] += sz; r['device'] |= top.endswith('.device')
    q = shlex.quote(root)
    for ln in adb.shell(f'[ -d {q} ] && cd {q} && for d in */; do [ -d "$d" ] && stat -c "%Y %n" "${{d%/}}"; done; true', check=False).splitlines():
        t, _, name = ln.partition(' ')
        if t.isdigit() and not name.startswith('.') and not name.endswith('.device'): rows.setdefault(name, {'files': 0, 'bytes': 0, 'device': False})['time'] = int(t)
    out = [dict(name=n, time=r.get('time', 0), **{k: r[k] for k in ('files', 'bytes', 'device')}) for n, r in rows.items() if 'time' in r]
    return sorted(out, key=lambda r: (r['time'], r['name']), reverse=True)


def _request(adb, package, file, lines, timeout=60):
    """Write saves/<file> with a fresh token, start the app, wait for saves/result.txt to answer that token → its KEY=VALUE pairs. The app ends after the request.
    An app ported before the save manager never answers (it starts its game instead): it is stopped and the request removed"""
    root, token = saves_folder(package), secrets.token_hex(6)
    adb.shell(f'am force-stop {package}')
    body = '\n'.join([*lines, f'request={token}', 'quit']) + '\n'
    adb.shell(f'mkdir -p {shlex.quote(root)} && printf %s {shlex.quote(body)} > {shlex.quote(root + "/" + file)}')
    adb.shell(f'am start -W -n {package}/android.app.NativeActivity >/dev/null 2>&1; true', timeout=60)
    t0 = time.time()
    while time.time() - t0 < timeout:
        kv = dict(ln.split('=', 1) for ln in adb.shell(f'cat {shlex.quote(root + "/result.txt")} 2>/dev/null; true', check=False).splitlines() if '=' in ln)
        if kv.get('request') == token:
            if kv.get('result') != 'ok': raise BuildError(f'应用无法完成该操作：{kv.get("error", "未给出原因")}')
            return kv
        time.sleep(0.5)
    adb.shell(f'am force-stop {package}; rm -f {shlex.quote(root + "/" + file)}', check=False)
    raise BuildError(f'{package} 在 {timeout} 秒内没有响应：在存档管理器之前移植的应用无法导出或导入存档（请重新移植），'
                     f'否则请查看其日志 {datapack.files_folder(package)}/log.txt')


def export(adb, package, dest, name=''):
    """A save folder from the device into dest/<name>/ (and <name>.device/): name, one already in saves/; none: a fresh export of the game's save. → the local folder"""
    root = saves_folder(package)
    if not name:
        say(f'{package}：正在停止应用并重新启动以导出存档（游戏本身不会启动）')
        name = _request(adb, package, 'export.txt', ['export'])['name']
        say(f'已在设备上导出为 saves/{name}')
    have = {r['name']: r for r in listing(adb, package)}
    if name not in have: raise BuildError(f'设备上没有存档文件夹 {name}（现有：{", ".join(have) or "无"}）')
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    for sub in [name] + ([name + '.device'] if have[name]['device'] else []):
        local = dest / sub
        if local.exists(): raise BuildError(f'{local} 已存在：请换一个文件夹')
        sizes = adb.remote_sizes(f'{root}/{sub}')
        local.mkdir()
        for rel in sorted(sizes):   # file by file: adb pull of a folder into one that exists puts it one level down, and empty folders matter to no save
            (local / rel).parent.mkdir(parents=True, exist_ok=True)
            adb.run('pull', f'{root}/{sub}/{rel}', local / rel, timeout=600)
            if (local / rel).stat().st_size != sizes[rel]: raise BuildError(f'{rel}：已取回 {(local / rel).stat().st_size} 字节，设备上为 {sizes[rel]} 字节')
    r = have[name]
    say(f'{name}：{r["files"]} 个文件，{r["bytes"]:,} 字节 → {dest / name}' + (f'（以及设备侧存档 {dest / (name + ".device")}）' if r['device'] else ''))
    return dest / name


def _unpacked(src, tmp):
    """A save folder from src: a folder as it is, or a .zip's contents (the one folder in it when it holds only that)"""
    src = Path(src)
    if src.is_dir(): return src
    if src.suffix.lower() != '.zip' or not src.is_file(): raise BuildError(f'{src}：不是文件夹，也不是 .zip')
    with zipfile.ZipFile(src) as z:
        for n in z.namelist():
            if n.startswith(('/', '\\')) or '..' in Path(n).parts: raise BuildError(f'{src}：{n} 指向压缩包之外')
        z.extractall(tmp)
    top = [p for p in Path(tmp).iterdir() if p.name != '__MACOSX']
    return top[0] if len(top) == 1 and top[0].is_dir() else Path(tmp)


def import_(adb, package, src, name=''):
    """A save folder (or .zip) from the PC as the game's save: pushed into saves/, then the app imports it at a start of its own → the device's report"""
    with tempfile.TemporaryDirectory(prefix='nx2a_save_') as tmp:
        folder = _unpacked(src, tmp)
        files = [p for p in folder.rglob('*') if p.is_file()]
        if not files: raise BuildError(f'{src}：其中没有文件')
        device = Path(str(folder) + '.device') if Path(src).is_dir() else None
        name = name or (Path(src).stem if not Path(src).is_dir() else folder.name)
        name = re.sub(r'[^A-Za-z0-9._ +()-]', '_', name).lstrip('.').removesuffix('.device') or 'save'
        if not NAME_OK.match(name): name = name[:60]
        taken = {r['name'] for r in listing(adb, package)}
        base, k = name, 2
        while name in taken: name, k = f'{base}-{k}', k + 1
        root = saves_folder(package)
        say(f'正在推送 {len(files)} 个文件（{sum(p.stat().st_size for p in files):,} 字节）到 saves/{name}')
        adb.push_tree(folder, f'{root}/.{name}.push')   # under a hidden name first: the app's picker never lists a folder half pushed
        if device and device.is_dir(): adb.push_tree(device, f'{root}/.{name}.push.device')
        q = lambda s: shlex.quote(f'{root}/{s}')
        adb.shell(f'mv {q("." + name + ".push")} {q(name)}' + (f' && mv {q("." + name + ".push.device")} {q(name + ".device")}' if device and device.is_dir() else ''))
    say(f'{package}：正在停止应用并重新启动以导入存档（会先导出将被替换的存档，然后退出）')
    kv = _request(adb, package, 'import.txt', [name])
    say(f'已导入 {name}（{kv.get("files", "?")} 个文件）' + (f'；被替换的存档在设备上是 saves/{kv["backup"]}' if kv.get('backup') else '') + '：请在设备上打开游戏')
    return kv


def connect(adb_exe, serial=''):
    if not adb_exe: raise BuildError('未找到 adb：请安装 Android platform-tools，或用 `porter config adb <路径>` 设置其路径')
    adb = Adb(adb_exe, serial or None, log=say)
    try: adb.pick()
    except AdbError as e: raise BuildError(str(e)) from e
    return adb
