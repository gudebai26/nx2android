# Installing a port on a device over adb: the APK, then the data pack into the app's files folder (porter/adb.py pushes it in resumable pieces).
# The player's own files there are kept: the pause menu's settings.txt, the logs, the Vulkan pipeline caches; saves are in the app's internal storage, which an update keeps.
# --romfs-at <folder>: the app reads the game's RomFS from a folder already on the device (a game dump an emulator also reads, a folder on the SD card) instead of a copy in
# its files folder, which saves that copy's 7 to 18 GB. The folder must hold exactly the pack's RomFS, names and sizes and a sample's content (--romfs-push puts it there), the app gets All files
# access over adb, and the installer writes DM_ROMFS=<folder> into the device's env.txt under ROMFS_MARK. The pack's env.txt is the same on every device, and the folder is
# a fact of one device, so the row is the installer's, not the pack's: a later install without --romfs-at carries it over from the device's env.txt (and checks the folder
# again), and --romfs-at app moves the RomFS back into the files folder. The DLC (aoc/) stays in the files folder: small for most games, and laid out by the port.
import hashlib, posixpath, re, shlex, tempfile, zipfile
from pathlib import Path

from porter import datapack
from porter.adb import Adb, AdbError, long_path
from porter.upstream import BuildError, PROG, say

APP = 'app'                                                  # --romfs-at app: the RomFS in the app's files folder (the default)
ROMFS_MARK = '# porter install --romfs-at'                   # in the device's env.txt: the DM_ROMFS row under it is the installer's, carried over by later installs
ACCESS = 'android.permission.MANAGE_EXTERNAL_STORAGE'        # All files access, which the APK asks for (mkapk.py) and the installer grants
ACCESS_HOW = '设置 > 应用 > {name} > 所有文件访问权限（Android 10：权限 > 文件和媒体）'
SAMPLE_FILES, SAMPLE_MAX = 256, 4 << 20                      # the folder's content check: up to this many of its files, each of at most this many bytes (romfs_sample)


def romfs_target(arg, files=None):
    """--romfs-at's folder, checked before anything touches the device: None for the app's files folder (app, or that folder's own RomFS). files: the app's files folder"""
    if arg is None or arg == APP: return None
    if arg != arg.strip() or any(c in arg for c in '\r\n\0') or not arg.startswith('/'):
        raise BuildError(f'--romfs-at 需要设备上文件夹的绝对路径（/sdcard/…、/storage/…），或 app：{arg!r}')
    d = posixpath.normpath(arg)
    if files and d == f'{files}/{datapack.ROMFS}': return None
    if files and (d + '/').startswith(files + '/'): raise BuildError(f'--romfs-at {d}：应用的 files 文件夹归安装程序自身管理；用 --romfs-at app 把 RomFS 保留在那里')
    if not d.startswith(('/sdcard/', '/storage/')): raise BuildError(f'--romfs-at {d}：应用只能读取共享存储，即 /sdcard/… 或 /storage/…（SD 卡为 /storage/<其 id>/…）')
    if re.search(r'/Android/(data|obb)(/|$)', d, re.I):   # FUSE keeps every app out of the others' Android/data and Android/obb, All files access or not
        raise BuildError(f'--romfs-at {d}：Android 不允许任何应用读取其他应用 Android/data 或 Android/obb 下的文件夹，即使有“所有文件访问权限”：'
                         '请把游戏文件移到别处（/sdcard/Games/…），或用 --romfs-at app 保留在本应用的 files 文件夹中')
    if len(d.encode()) > 512: raise BuildError(f'--romfs-at：文件夹路径最长 512 字节（宿主库的路径缓冲区）：{d}')
    return d


def device_romfs(env_text):   # the folder an earlier --romfs-at install wrote into the device's env.txt (the DM_ROMFS row under ROMFS_MARK), None when there is none
    lines, out = env_text.splitlines(), None
    for prev, ln in zip([''] + lines, lines):
        if prev.startswith(ROMFS_MARK) and ln.startswith('DM_ROMFS='): out = ln[len('DM_ROMFS='):].rstrip() or None
    return out


def env_with_romfs(pack_env, folder):   # the pack's env.txt with the installer's row last: the app applies the rows in order (front_android.c env_file), so it wins
    return pack_env.rstrip('\n') + f'\n{ROMFS_MARK}: 游戏的 RomFS 从该文件夹读取（后续安装会保留；--romfs-at app 可把它移回 files 文件夹）\nDM_ROMFS={folder}\n'


def romfs_diff(want, have):   # want, have: {path in the RomFS: size} → (missing, of another size, not the game's), each sorted
    return sorted(want.keys() - have.keys()), sorted(r for r in want.keys() & have.keys() if want[r] != have[r]), sorted(have.keys() - want.keys())


def romfs_sample(rom, n=SAMPLE_FILES, max_size=SAMPLE_MAX):
    """The RomFS files whose content the installer compares by MD5 (romfs_changed): up to n of at most max_size bytes, spread evenly over the sorted paths. Names and
    sizes alone let a dump of another game version through when the files it changed kept their sizes; a sample of the small files catches that (at most SAMPLE_FILES x SAMPLE_MAX = 1 GB read on each side: seconds on internal storage, longer on an SD card)"""
    small = sorted(r for r, s in rom.items() if s <= max_size)
    return small if len(small) <= n else [small[i * len(small) // n] for i in range(n)]


def romfs_changed(adb, data, target, rom, there):
    """The sample's files that target holds at the pack's size but with other bytes (MD5), sorted; None when the device gave no MD5 at all (nothing compared)"""
    sample = [r for r in romfs_sample(rom) if there.get(r) == rom[r]]
    if not sample: return []
    got = adb.remote_md5(target, sample)
    if not got: return None
    return sorted(r for r in sample if r in got and got[r] != hashlib.md5(Path(long_path(Path(data) / datapack.ROMFS / r)).read_bytes()).hexdigest())


def romfs_mismatch(folder, want, have, n=5, changed=()):
    """What keeps folder's listing (have) from being the pack's RomFS (want), the first n of each kind; changed: files of the right size whose content differs
    (romfs_changed). '' when it is the same"""
    missing, other, extra = romfs_diff(want, have)
    if not have: return f'{folder} 中没有任何文件（不在设备上，或为空文件夹）'
    first = lambda xs, f=str: ', '.join(f(x) for x in xs[:n]) + (', …' if len(xs) > n else '')
    sizes = lambda r: f'{r}：{have[r]:,} 字节，而数据包中为 {want[r]:,}'
    parts = ([f'缺少游戏文件 {len(missing):,} 个（{first(missing)}）'] if missing else []) + \
            ([f'{len(other):,} 个文件大小不同（{first(other, sizes)}）'] if other else []) + \
            ([f'{len(changed):,} 个文件大小相同但内容不同（MD5：{first(list(changed))}）'] if changed else []) + \
            ([f'{len(extra):,} 个文件不属于本游戏（{first(extra)}）'] if extra else [])
    return f'{folder} 不是本数据包的 RomFS（共 {len(want):,} 个文件）：' + '；'.join(parts) if parts else ''


def apk_asks_access(apk):   # whether the APK asks for All files access: one made before mkapk.py did cannot be given it (`appops set` takes, but FUSE checks the permission)
    try:
        with zipfile.ZipFile(apk) as z: m = z.read('AndroidManifest.xml')
    except (OSError, KeyError, zipfile.BadZipFile): return False
    return ACCESS.encode('utf-16-le') in m or ACCESS.encode() in m


def install(apk, data, adb_exe, serial=None, replace_signer=False, romfs_at=None, romfs_push=False):
    """romfs_at: a folder on the device that holds the game's RomFS (romfs_push: push the pack's RomFS into it first), 'app' for the app's files folder, None: as the
    device has it (an earlier --romfs-at install's folder, else the files folder)"""
    apk, data = Path(apk), Path(data)
    man = datapack.read_manifest(data)
    if not man: raise BuildError(f'不是数据包（缺少 {datapack.MANIFEST}）：{data}')
    if not apk.is_file(): raise BuildError(f'找不到 APK：{apk}')
    if not adb_exe: raise BuildError('未找到 adb：请安装 Android platform-tools，或用 `porter config adb <路径>` 设置其路径')
    files = datapack.files_folder(man['package'])
    target = romfs_target(romfs_at, files)
    if romfs_push and not target: raise BuildError('--romfs-push 需要同时给出 --romfs-at <设备上位于应用 files 文件夹之外的文件夹>')
    adb = Adb(adb_exe, serial, log=say)
    PROG.stage('安装', step='正在查找设备')
    pushing = False
    try:
        dev = adb.pick()
        model = adb.prop('ro.product.model')
        say(f'设备：{model or dev}（{dev}），GPU {adb.gpu() or "未知"}')
        if 'bcn' in man.get('requires', []):   # the profile's requires: BC-compressed textures
            bc = adb.bc_textures()
            if bc is False: raise BuildError(f'{man["name"]} 需要 BC 压缩纹理，而该设备的 GPU（{adb.gpu()}）无法读取')
            if bc is None: say(f'[RED] {man["name"]} 需要 BC 压缩纹理；该 GPU（{adb.gpu() or "未知"}）是否支持尚不可知')
        was = device_romfs(adb.read_text(f'{files}/env.txt'))   # an earlier --romfs-at install's folder
        if romfs_at is None and was:
            target = romfs_target(was, files)
            if target: say(f'应用从 {target} 读取 RomFS（来自此前的 --romfs-at 安装；--romfs-at app 可把它移回应用的 files 文件夹）')
        rom = datapack.romfs_sizes(data) if target else {}
        missing = other = changed = []; there = {}
        if target:   # what can refuse comes before the APK goes in, so a refused install changes nothing on the device (but a new app's grant: below)
            if not rom: raise BuildError(f'{data} 中没有可与 {target} 比对的 RomFS')
            if not apk_asks_access(apk): raise BuildError(f'{apk.name} 未申请“所有文件访问权限”，而读取 {target} 需要该权限（该 APK 由更早的 nx2android 版本生成）：请重新移植')
            PROG.set(step=f'正在比对 {target} 与数据包的 RomFS')
            there = adb.remote_sizes(target)
            missing, other, extra = romfs_diff(rom, there)
            if not extra and (romfs_push or not (missing or other)):   # a listing that can be used: a sample of the files compared by content too
                PROG.set(step=f'正在比对 {target} 的内容与数据包的 RomFS')
                changed = romfs_changed(adb, data, target, rom, there)
                if changed is None: say(f'[RED] 设备未返回 {target} 中文件的 MD5：仅比对了名称与大小'); changed = []
            if bad := romfs_mismatch(target, rom, there, changed=changed):
                if extra or not romfs_push:
                    whose = f'{man["name"]} {man.get("display") or man["version"]}'
                    raise BuildError(f'{bad}。' + ('它来自此前一次 --romfs-at 安装。' if romfs_at is None else '') + f'请把 --romfs-at 指向 {whose} 的 RomFS 文件夹'
                                     + ('（--romfs-push 只添加文件，从不删除：请清空该文件夹，或换一个）' if extra else
                                        '，并加上 --romfs-push 以把数据包的 RomFS 推送到该文件夹') + '，或用 --romfs-at app 把 RomFS 保留在应用的 files 文件夹中')
                say(f'{target}：RomFS 的 {len(rom):,} 个文件中有 {len(missing) + len(other) + len(changed):,} 个需要推送（--romfs-push）')
            else: say(f'{target} 已包含本数据包的 RomFS（{len(rom):,} 个文件，{sum(rom.values()) / 2**30:.1f} GB）：不再推送')
        free = adb.free_bytes()
        have = adb.remote_sizes(files)
        if target:   # the RomFS is not pushed into the files folder; a copy an earlier install left there goes only after env.txt points elsewhere, so it is not counted as freed
            need = man['bytes'] - sum(rom.values()) - sum(s for r, s in have.items() if not r.startswith(datapack.ROMFS + '/'))
            if push := sum(rom[r] for r in missing + other + changed):
                if adb.same_volume('/sdcard', target): need += push
                elif (tfree := adb.free_bytes(target)) is not None and push > tfree: raise BuildError(f'{target} 所在卷可用 {tfree / 2**30:.1f} GB；RomFS 还需要 {push / 2**30:.1f} GB')
        else: need = man['bytes'] - sum(have.values())
        if free is not None and need > free: raise BuildError(f'设备可用空间 {free / 2**30:.1f} GB；游戏还需要 {need / 2**30:.1f} GB')
        if target and not replace_signer and adb.installed(man['package']):   # an update: the grant (the app's uid's, which the update keeps) before the APK goes in
            PROG.set(step='正在授予应用“所有文件访问权限”')
            ok, state = adb.storage_access(man['package'], grant=True)
            if not ok and adb.requests_permission(man['package'], ACCESS if adb.sdk() >= 30 else 'android.permission.READ_EXTERNAL_STORAGE'):
                raise BuildError(f'应用需要“所有文件访问权限”才能读取 {target}，而设备未通过 adb 授予（{state}）：请在设备上允许'
                                 f'（{ACCESS_HOW.format(name=man["name"])}），然后重新安装')
        PROG.set(step=f'正在安装 {apk.name}')
        adb.install(apk, man['package'], replace_signer=replace_signer)
        PROG.set(step='正在让应用创建其 files 文件夹')
        if adb.prepare_app_files(man['package']): say('已启动应用一次，以便 Android 创建其 files 文件夹（缺少数据时它会立即退出）')
        if target:   # after the install too: a new package, one --replace-signer made again, or an update from an APK that did not ask for the access (granted above
            # when it did) has no grant yet. Before the push, so a refusal leaves the data as it was: a new app has none, and an older APK's update is the one case
            # that cannot be refused before the APK goes in
            PROG.set(step='正在授予应用“所有文件访问权限”')
            ok, state = adb.storage_access(man['package'], grant=True)
            if not ok: raise BuildError(f'应用需要“所有文件访问权限”才能读取 {target}，而设备未通过 adb 授予（{state}）：请在设备上允许'
                                        f'（{ACCESS_HOW.format(name=man["name"])}），然后重新安装')
            say(f'{state}：应用从 {target} 读取 RomFS')
        PROG.set(step='正在推送游戏数据', done=0, total=man['bytes'] - sum(rom.values()), unit='B'); pushing = True
        have = adb.remote_sizes(files)   # again: an install with --replace-signer uninstalls first, and Android deletes the files folder with the app
        adb.push_tree(data, files, keep=datapack.KEEP_ON_DEVICE, skip=(datapack.MANIFEST,) + (('env.txt',) if target else ()), exclude=(datapack.ROMFS,) if target else (),
                      verify=lambda rel: rel.split('/', 1)[0] not in ('RomFS', 'aoc'),   # the game's own files change only with their version
                      progress=lambda d, t, note: PROG.set(done=d, total=t, note=note), have=have)
        if target and (missing or other or changed):   # --romfs-push: the pack's RomFS into the folder (changed: pushed again when their MD5 differs), then the folder checked again
            PROG.set(step=f'正在把 RomFS 推送到 {target}', done=0, total=sum(rom[r] for r in missing + other + changed), unit='B')
            again = set(changed)
            adb.push_tree(data / datapack.ROMFS, target, verify=lambda rel: rel in again, progress=lambda d, t, note: PROG.set(done=d, total=t, note=note), have=there)
            now = adb.remote_sizes(target)
            if bad := romfs_mismatch(target, rom, now, changed=romfs_changed(adb, data, target, rom, now) or []): raise BuildError(f'推送后，{bad}')
        if target:   # the pack's env.txt with the installer's row
            with tempfile.TemporaryDirectory(prefix='nx2a_env_') as t:
                env = Path(t) / 'env.txt'; env.write_text(env_with_romfs((data / 'env.txt').read_text(encoding='utf-8'), target), encoding='utf-8', newline='\n')
                adb.push_file(env, f'{files}/env.txt')
        elif was and romfs_at == APP: say(f'应用已改回从自己的 files 文件夹读取 RomFS；{was} 保持原样')
        stale = [rel for rel in have if rel.split('/', 1)[0] in datapack.OWNED and not (target and rel.startswith(datapack.ROMFS + '/')) and not (data / rel).is_file()]   # the push only adds the pack's own files, which are never stale
        if stale:   # an older version's files, which the game would still find
            PROG.set(step=f'正在删除 {len(stale):,} 个过期文件')
            for i in range(0, len(stale), 64): adb.shell(f'cd {shlex.quote(files)} && rm -f ' + ' '.join(shlex.quote(r) for r in stale[i:i + 64]))
        if old := [r for r in have if r.startswith(datapack.ROMFS + '/')] if target else []:   # a second copy of the game, now that env.txt points at target
            PROG.set(step='正在删除 files 文件夹中的 RomFS 副本')
            adb.shell(f'rm -rf {shlex.quote(f"{files}/{datapack.ROMFS}")}', timeout=1200)
            say(f'已从应用的 files 文件夹删除此前的 RomFS 副本（{len(old):,} 个文件，{sum(have[r] for r in old) / 2**30:.1f} GB）')
        adb.push_file(data / datapack.MANIFEST, f'{files}/{datapack.MANIFEST}')   # last: a pack on the device with its manifest is a complete one
    except AdbError as e:
        try: gone = pushing and adb.serial in [s for s, st, _ in adb.devices() if st == 'device'] and not adb.installed(man['package'])   # a device that went away is not an uninstall
        except AdbError: gone = False
        if gone:   # Android deletes an app's files folder with the app: a push into it then fails part-way (a size mismatch)
            raise BuildError(f'推送数据时 {man["package"]} 在设备上被卸载了（{e}）') from e
        raise BuildError(str(e)) from e
    PROG.end(f'{man["name"]} 已安装到 {model or dev}')
    say(f'安装完成：请在设备上打开“{man["name"]}”')
