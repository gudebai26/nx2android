# The command line (python -m porter …). Every command the GUI uses is here, so a port runs the same headless. Exit codes: 0 done, 1 error, 130 interrupted.
import argparse, json, os, sys
from pathlib import Path


def _parser():
    ap = argparse.ArgumentParser(prog='porter', description='把你自己的 Switch 游戏文件移植为 Android 应用（见 docs/ARCHITECTURE.md）。')
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('doctor', help='检查移植所需的工具'); d.add_argument('--json', action='store_true'); d.add_argument('-k', '--keys')
    p = sub.add_parser('profiles', help='列出可移植的游戏'); p.add_argument('--all', action='store_true', help='同时列出尚未移植到 Android 的 iOS 配置'); p.add_argument('--json', action='store_true')
    i = sub.add_parser('inspect', help='查看游戏文件夹的内容，以及是否可以移植'); i.add_argument('folder'); i.add_argument('--json', action='store_true')
    i.add_argument('-k', '--keys', help='prod.keys（默认使用已配置的）：用于读取文件名中不含 [title ID] 的转储的 title ID')
    po = sub.add_parser('port', help='生成 APK 与数据包（并可安装）')
    po.add_argument('folder', help='游戏的文件夹：其中的本体 NSP / XCI / NSZ / XCZ、更新与 DLC')
    po.add_argument('-k', '--keys', help='prod.keys（默认使用已配置的）')
    po.add_argument('-o', '--out', help='APK 与数据包的输出文件夹（默认：游戏文件夹）')
    po.add_argument('-p', '--version', default='', help='要打包的版本，v<N>（默认：文件夹中最新的版本）')
    po.add_argument('-l', '--language', default='', help='游戏语言，代码或名称（默认：英语）')
    po.add_argument('--app-name', default='', help='应用在设备上显示的名称（默认：游戏名称）')
    po.add_argument('--install', action='store_true', help='完成后安装到已连接的设备')
    po.add_argument('--device', default='', help='要安装到的设备的 adb 序列号')
    po.add_argument('--replace-signer', action='store_true', help='先卸载同包名但由其他密钥签名的应用（会删除其存档）')
    po.add_argument('--debug', action='store_true', help='开发版本：包含调试信息、可调试、支持 DM_CTL 按键')
    po.add_argument('--experimental', action='store_true', help='尝试尚未移植到 Android 的游戏（配置由文件扫描推测，可能无法启动）')
    po.add_argument('--package', default='', help='使用指定的包名而非配置中的包名（可与游戏的另一个版本并存用于测试）')
    po.add_argument('--external-display', action='store_true', help='构建外接屏手柄 Activity（需要 Android SDK 的 platform 与 build-tools）')
    po.add_argument('--fps60', action='store_true', help='60 fps：优先使用配置自带的帧率上限，否则使用社区 FPSLocker-Warehouse 中该游戏版本的配置，从 GitHub 获取')
    po.add_argument('--touch', action='store_true',
                    help='触屏游戏：屏幕手柄启动时隐藏，除菜单按钮外的所有触摸都交给游戏（暂停菜单可再次显示；'
                         '这只是首次启动的默认值，应用会保留玩家自己的设置）')
    po.add_argument('--mt-replay', action='store_true',
                    help='多线程回放：在独立线程上录制游戏的 Vulkan 命令（env.txt 中 DM_VK_MT_REPLAY=1），适用于回放线程拖慢帧率的游戏；'
                         '需重新移植（或手动修改 env.txt）才能关闭')
    po.add_argument('--vulkan-driver', default=None, metavar='FILE',
                    help='打包进 APK、用来替代设备自带驱动的 arm64 Vulkan 驱动库（.so，例如适用于 Adreno 的 Mesa Turnip）'
                         '（env.txt 中的 DM_VK_DRIVER）；库加载失败时应用会退回设备驱动。名称不是 lib*.so 的库'
                         '（vulkan.purple.so）将按 lib<名称> 打包，因为 Android 只解压这种名称')
    po.add_argument('--shared-font', default=None, metavar='FILE',
                    help='作为游戏所请求的系统字体打包的 TrueType 字体（.ttf，单个字体而非字体集），替代设备自带的 Roboto：包含中日韩'
                         '字符的字体可显示游戏通过系统字体绘制的中文与韩文（env.txt 中的 DM_SHARED_FONT）；本工具从不自带此类字体')
    po.add_argument('--firmware', default=None, metavar='DIR',
                    help="存放主机系统内容文件（.nca，即固件转储的 NAND SYSTEM Contents/registered）的文件夹，供读取系统数据的游戏使用"
                         "（android.tsv 的 sysdata：马力欧卡丁车 8 豪华版的 Mii 头像来自 MiiModel）；本工具从不自带（默认：已配置的路径）")
    po.add_argument('--vercode-offset', type=int, default=None, metavar='N',
                    help='给 APK 的 versionCode 加上 N（0-65535），使同一游戏版本的重建包可以覆盖安装 versionCode 更高的包并保留存档'
                         '（默认：$NX2A_VERCODE_OFFSET，否则为 0）')
    po.add_argument('--alias-headroom', type=int, default=None, metavar='GB',
                    help='额外的客户机地址空间（别名区域），单位为 GB（0-48），适用于使用虚拟地址内存的游戏，用于日志中出现 '
                         '"AllocateAddressRegion refused (0x1803)" 之后崩溃的游戏；它不占用实际内存（默认：配置文件未'
                         '测试过的游戏版本为 8，测试过的为 0）')
    _romfs_at(po, '配合 --install 使用：')
    po.add_argument('--jobs', type=int, default=os.cpu_count() or 8)
    po.add_argument('--progress', default='auto', choices=('auto', 'bar', 'lines', 'json', 'off'), help='json：每行一个 JSON 事件，供图形界面使用')
    ins = sub.add_parser('install', help='安装此前移植好的成果'); ins.add_argument('data', help='数据包文件夹（<App>-data）'); ins.add_argument('--apk', help='APK（默认：数据包旁的 <App>.apk）')
    ins.add_argument('--device', default=''); ins.add_argument('--replace-signer', action='store_true'); ins.add_argument('--progress', default='auto', choices=('auto', 'bar', 'lines', 'json', 'off'))
    _romfs_at(ins)
    dv = sub.add_parser('devices', help='adb 能看到的设备'); dv.add_argument('--json', action='store_true')
    lb = sub.add_parser('library', help='列出某个文件夹中已移植的游戏：数据包（nx2a-data.json）与同名 APK，供“推送”使用')
    lb.add_argument('folder', nargs='?', default='', help='要扫描的文件夹（默认：设置中的游戏库文件夹，否则当前目录）')
    lb.add_argument('--json', action='store_true')
    t = sub.add_parser('tools', help='列出或下载缺失的工具（Android NDK、.NET SDK、platform-tools），来源为 Google 与 Microsoft')
    t.add_argument('--download', action='store_true', help='下载并解压到 nx2android 文件夹（即表示你接受其许可证）')
    t.add_argument('--only', default='', help='只处理这些工具而非全部缺失项：ndk、dotnet、adb（逗号分隔）'); t.add_argument('--json', action='store_true')
    t.add_argument('--progress', default='auto', choices=('auto', 'bar', 'lines', 'json', 'off'))
    c = sub.add_parser('config', help='查看或修改已保存的设置'); c.add_argument('key', nargs='?'); c.add_argument('value', nargs='?')
    sv = sub.add_parser('saves', help="在设备与电脑之间转移游戏存档：列出、导出、导入（主机存档亦可）")
    svs = sv.add_subparsers(dest='saves_cmd', required=True)
    sa = svs.add_parser('apps', help='设备上已安装的移植游戏')
    sl = svs.add_parser('list', help="应用 files 文件夹中 saves/ 下的存档文件夹，最新的在前")
    se = svs.add_parser('export', help="把游戏存档导出到本地文件夹：重新导出（会停止应用、启动导出后退出），或用 --name 取出已有的一个")
    se.add_argument('dest', nargs='?', default='.', help='存放的文件夹（默认：当前目录）'); se.add_argument('--name', default='', help='不重新导出，改为取设备上已有的某个存档文件夹')
    si = svs.add_parser('import', help="把存档文件夹或 .zip 作为游戏存档导入（主机存档按其存档工具的复制结构，或一次导出）；被替换的存档会先在设备上导出")
    si.add_argument('src', help='存档文件夹（其文件位于最上层，与游戏保存的结构一致）或它的 .zip；旁边的 <folder>.device 为设备侧存档')
    si.add_argument('--name', default='', help="在设备上的文件夹名（默认：文件夹或 .zip 的名称）")
    for x in (sa, sl, se, si):
        x.add_argument('--device', default='', help='adb 序列号（默认：已配置的设备，否则为唯一连接的设备）'); x.add_argument('--json', action='store_true')
        if x is not sa: x.add_argument('--package', default='', help='应用（默认：唯一已安装的移植游戏）')
    dr = sub.add_parser('driver', help='在不改动游戏文件的前提下为已移植的应用更换 Vulkan 驱动：重新打包并签名其 APK（存档保留）')
    drg = dr.add_mutually_exclusive_group(required=True)
    drg.add_argument('library', nargs='?', help='驱动库（.so，例如 Mesa 的 Turnip）；该应用必须曾用 --vulkan-driver 移植过')
    drg.add_argument('--system', action='store_true', help='不使用驱动库：恢复设备自带驱动')
    dr.add_argument('--package', default='', help='应用（默认：唯一已安装的移植游戏）')
    dr.add_argument('--device', default='', help='adb 序列号（默认：已配置的设备，否则为唯一连接的设备）'); dr.add_argument('--json', action='store_true')
    return ap


def _romfs_at(p, when=''):   # install's --romfs-at / --romfs-push (porter/install.py)
    p.add_argument('--romfs-at', default=None, metavar='FOLDER',
                   help=when + '从设备上的该文件夹读取游戏的 RomFS（例如 /sdcard/Games/<game>/romfs，即其他应用也读取的转储，或 SD 卡上的文件夹），'
                        '而不再使用应用 files 文件夹中的副本：该文件夹必须正好是数据包的 RomFS（文件的名称、大小以及抽样内容都一致），应用会被授予'
                        '“所有文件访问权限”，此前安装留在 files 文件夹中的副本会被删除。之后的安装会保留该文件夹；填“app”可把 RomFS 移回 files 文件夹')
    p.add_argument('--romfs-push', action='store_true', help='配合 --romfs-at：先把数据包的 RomFS 推送到该文件夹（空文件夹，或本游戏的文件夹；其中内容不会被删除）')


def _print_json(obj): print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_doctor(a):
    from porter import toolchain
    rows = toolchain.checks(keys=a.keys)
    if a.json: _print_json([r.as_dict() for r in rows])
    else:
        for r in rows: print(f'{"ok " if r.ok else ("!! " if r.required else "-- ")} {r.name:15} {r.detail}' + (f'\n{"":19}→ {r.fix}' if r.fix and not r.ok else ''))
    return 0 if all(r.ok or not r.required for r in rows) else 1


def cmd_profiles(a):
    from porter import profiles
    rows = profiles.all_entries(android_only=not a.all)
    if a.json: _print_json([r.as_dict() for r in rows]); return 0
    for r in rows: print(f'{r.id}  {r.status:12} {r.name}' + (f'  ({r.tested})' if r.tested else ''))
    return 0


def cmd_inspect(a):
    import contextlib
    from porter import pipeline
    from porter.upstream import BuildError
    try:
        with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):
            info = pipeline.inspect(a.folder, a.keys)   # keep JSON stdout one document for the GUI
    except BuildError as e:
        if a.json: _print_json({'error': str(e)})
        else: print(e, file=sys.stderr)
        return 2
    if a.json: _print_json(info); return 0 if info['supported'] else 2
    print(f'{info["title_id"]}  {info.get("name", "")}\n  本体：{info["base"]}')
    for u in info['updates']: print(f'  更新 {u["version"]}：{u["file"]}')
    for d in info['dlc']: print(f'  DLC {d["title_id"]}：{d["file"]}')
    if info.get('pack'): print(f'  可打包 {info["pack"]}（文件夹中的版本：{", ".join(info["versions"])}）')
    if info['android']: print(f'  Android：{info["android"]["status"]}' + (f'，测试设备 {info["android"]["tested"]}' if info['android']['tested'] else ''))
    if info['reason']: print(f'  {info["reason"]}')
    if info.get('experimental'): print('  可用 porter port --experimental 尝试')
    return 0 if info['supported'] else 2


def _with_progress(mode, fn):
    from porter.upstream import BuildError, PROG, nsp
    PROG.mode = nsp.progress_mode(mode)
    environ = dict(os.environ)
    try:
        fn(); return 0
    except BuildError as e:
        PROG.close(ok=False); PROG.say(f'[RED] {e}'); return 1
    except KeyboardInterrupt:
        PROG.close(ok=False); PROG.say('[RED] 已中断'); return 130
    except Exception as e:
        PROG.close(ok=False)
        if PROG.mode == 'json': PROG.say(f'[RED] {type(e).__name__}: {e}')
        raise
    finally:
        PROG.close(ok=False)
        for k in os.environ.keys() - environ.keys(): del os.environ[k]
        os.environ.update(environ)


def cmd_port(a):
    from porter import pipeline
    offset = a.vercode_offset
    if offset is None:
        env = os.environ.get('NX2A_VERCODE_OFFSET', '').strip() or '0'
        try: offset = int(env)
        except ValueError: print(f'NX2A_VERCODE_OFFSET 不是数字：{env}', file=sys.stderr); return 2
    if (a.romfs_at is not None or a.romfs_push) and not a.install: print('--romfs-at 与 --romfs-push 属于安装选项：请加上 --install，或在移植后运行 porter install', file=sys.stderr); return 2
    from porter import install
    from porter.upstream import BuildError
    try: install.romfs_target(a.romfs_at)   # a folder the app cannot read fails now, not after the shaders
    except BuildError as e: print(e, file=sys.stderr); return 2
    opts = pipeline.Options(folder=Path(a.folder), keys=Path(a.keys) if a.keys else None, firmware=Path(a.firmware) if a.firmware else None, out=Path(a.out) if a.out else None, version=a.version, language=a.language,
                            app_name=a.app_name, install=a.install, device=a.device, replace_signer=a.replace_signer, debug=a.debug, experimental=a.experimental, package=a.package, fps60=a.fps60, touch=a.touch, mt_replay=a.mt_replay, vulkan_driver=a.vulkan_driver, shared_font=a.shared_font,
                            vercode_offset=offset, alias_headroom=a.alias_headroom, romfs_at=a.romfs_at, romfs_push=a.romfs_push, external_display=a.external_display, jobs=a.jobs)
    return _with_progress(a.progress, lambda: pipeline.emit_result(pipeline.Port(opts).run()))


def cmd_install(a):
    from porter import config, datapack, install, toolchain
    from porter.upstream import BuildError, PROG
    def go():
        data = Path(a.data).resolve()
        apk = Path(a.apk) if a.apk else data.with_name(data.name.removesuffix('-data') + '.apk')
        PROG.plan(1)
        cfg = config.load()
        install.install(apk, data, toolchain.find_adb(cfg), a.device or cfg.get('device'), replace_signer=a.replace_signer, romfs_at=a.romfs_at, romfs_push=a.romfs_push)
    return _with_progress(a.progress, go)


def cmd_devices(a):
    from porter import config, toolchain
    from porter.adb import Adb, AdbError
    exe = toolchain.find_adb()
    if not exe: print('未找到 adb', file=sys.stderr); return 1
    try: rows = Adb(exe).devices()
    except AdbError as e: print(e, file=sys.stderr); return 1
    if a.json: _print_json([{'serial': s, 'state': st, 'info': d} for s, st, d in rows]); return 0
    for s, st, d in rows: print(f'{s}\t{st}\t{d}')
    if not rows: print('没有设备')
    return 0


def cmd_library(a):
    from porter import library
    root = a.folder or config.load().get('library') or '.'
    rows = library.scan(root)
    if a.json: _print_json(rows); return 0
    if not rows: print(f'{root} 中没有已移植的游戏（需要 nx2a-data.json 与同名的 .apk）'); return 0
    for r in rows:
        print(f'{r["name"]}  {r["display"] or r["version"]}  {r["package"]}  {r["bytes"] / 2**30:.1f} GB')
        print(f'  数据包：{r["data"]}')
        print(f'  APK：{r["apk"] or "（缺少 APK：请重新移植或把它放回数据包旁）"}')
    return 0


def cmd_tools(a):
    from porter import fetch
    from porter.upstream import BuildError
    names = [{'ndk': 'Android NDK', 'dotnet': '.NET SDK', 'adb': 'adb'}[n.strip()] for n in a.only.split(',') if n.strip()] or None
    if a.download:
        def go():
            items = fetch.plan(names)
            if not items: fetch.PROG.say('无需下载：所有工具均已就绪'); return
            for it in items: fetch.PROG.say(f'{it.name} {it.version} ({it.size / 2**20:,.0f} MB) from {it.url}, license: {it.license}')
            fetch.download(items)
        return _with_progress(a.progress, go)
    try: items = fetch.plan(names)
    except (BuildError, OSError) as e: print(f'无法读取下载列表：{e}', file=sys.stderr); return 1
    if a.json: _print_json([it.as_dict() for it in items]); return 0
    for it in items: print(f'{it.name:12} {it.version:16} {it.size / 2**20:8,.0f} MB  {it.url}\n{"":12} license: {it.license}')
    print('运行 porter tools --download 可下载它们' if items else '无需下载：所有工具均已就绪')
    return 0


def cmd_config(a):
    from porter import config
    if a.key is None:
        for k, v in config.load().items(): print(f'{k:9} {v or "-":40}  {config.KEYS[k]}')
        print(f'({config.path()})'); return 0
    if a.value is None: print(config.load().get(a.key, '')); return 0
    try: config.set_value(a.key, a.value)
    except KeyError as e: print(e.args[0], file=sys.stderr); return 1
    return 0


def cmd_saves(a):
    import contextlib
    from porter import config, saves, toolchain
    from porter.upstream import BuildError
    try:
        with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):   # with --json, stdout is the one JSON document (the GUI parses it whole)
            cfg = config.load(); adb = saves.connect(toolchain.find_adb(cfg), a.device or cfg.get('device'))
            if a.saves_cmd == 'apps': out = saves.apps(adb)
            else:
                pkg = saves.pick_app(adb, a.package)
                if a.saves_cmd == 'list': out = saves.listing(adb, pkg)
                elif a.saves_cmd == 'export': out = {'package': pkg, 'folder': str(saves.export(adb, pkg, Path(a.dest), a.name))}
                else: out = dict(saves.import_(adb, pkg, Path(a.src), a.name), package=pkg)
    except BuildError as e:
        if a.json: _print_json({'error': str(e)})
        else: print(e, file=sys.stderr)
        return 1
    if a.json: _print_json(out)
    elif a.saves_cmd == 'apps': print('\n'.join(out) or '没有安装任何移植游戏')
    elif a.saves_cmd == 'list':
        import time
        for r in out: print(f'{time.strftime("%Y-%m-%d %H:%M", time.localtime(r["time"]))}  {r["name"]:32} {r["files"]:5} 个文件 {r["bytes"] / 2**20:9.2f} MB' + ('  （+ 设备存档）' if r['device'] else ''))
        if not out: print(f'设备上没有存档文件夹（{saves.saves_folder(pkg)}）')
    return 0


def cmd_driver(a):
    import contextlib
    from porter import config, driver, saves, toolchain
    from porter.upstream import BuildError
    try:
        with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):   # with --json, stdout is the one JSON document
            cfg = config.load(); adb = saves.connect(toolchain.find_adb(cfg), a.device or cfg.get('device'))
            out = driver.swap(adb, saves.pick_app(adb, a.package), None if a.system else a.library)
    except BuildError as e:
        if a.json: _print_json({'error': str(e)})
        else: print(e, file=sys.stderr)
        return 1
    if a.json: _print_json(out)
    return 0


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # game names and progress bars on a Windows console
    a = _parser().parse_args(argv)
    return {'doctor': cmd_doctor, 'profiles': cmd_profiles, 'inspect': cmd_inspect, 'port': cmd_port, 'install': cmd_install, 'devices': cmd_devices, 'library': cmd_library, 'tools': cmd_tools, 'config': cmd_config, 'saves': cmd_saves, 'driver': cmd_driver}[a.cmd](a)
