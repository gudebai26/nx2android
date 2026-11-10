# The tools a port needs on the player's machine, found and checked (`porter doctor`, the GUI's setup screen). Nothing here is shipped with the tool:
# the player installs the Android NDK (r27c), the .NET SDK (10) and, to install from the tool, platform-tools (adb), or has `porter tools --download` (porter.fetch)
# fetch them from Google and Microsoft into their nx2android folder (paths.player_tools()), which the finders here also search.
import glob, importlib.util, os, re, shutil, subprocess, sys
from dataclasses import dataclass, field
from pathlib import Path

from porter import config
from porter.paths import LOCAL_TOOLS, player_tools

NDK_REVISION = '27.2.12479018'   # r27c: the NDK the Android host is built and tested with
NDK_RELEASE = 'r27c'
DOTNET_MAJOR = 10
PY_MODULES = {'lz4.block': 'lz4', 'capstone': 'capstone'}   # the rewriter (capstone) and the NSO reader (lz4)
EXE = '.exe' if os.name == 'nt' else ''
DISK_GB = 20   # the setup check's free space: a NEO-sized port (7 GB of build folder) with room to spare


class ToolError(Exception):
    pass


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True
    path: str = ''
    fix: str = ''

    def as_dict(self): return dict(self.__dict__)


def _sdk_roots():   # Android SDK folders that may hold ndk/<version> and platform-tools
    out = [os.environ.get(k) for k in ('ANDROID_HOME', 'ANDROID_SDK_ROOT')]
    if os.name == 'nt': out.append(os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Android', 'Sdk'))
    elif sys.platform == 'darwin': out.append(os.path.expanduser('~/Library/Android/sdk'))
    else: out.append(os.path.expanduser('~/Android/Sdk'))
    return [Path(p) for p in out if p]


def ndk_revision(d):   # Pkg.Revision from an NDK folder's source.properties, None when it is not an NDK
    try: t = (Path(d) / 'source.properties').read_text(errors='replace')
    except OSError: return None
    m = re.search(r'Pkg\.Revision\s*=\s*([\d.]+)', t)
    return m.group(1) if m else None


def find_ndk(cfg=None):
    """(folder, revision) of the NDK to build with: the configured one, ANDROID_NDK_HOME / ANDROID_NDK_ROOT, then the usual install places; r27c is preferred when several are found"""
    cfg = cfg if cfg is not None else config.load()
    given = [cfg.get('ndk'), os.environ.get('ANDROID_NDK_HOME'), os.environ.get('ANDROID_NDK_ROOT')]
    for d in filter(None, given):   # an explicit choice is taken as it is, even when it is not r27c (the check says so)
        if ndk_revision(d): return Path(d), ndk_revision(d)
    found = [Path(p) for t in (LOCAL_TOOLS, player_tools()) for p in glob.glob(str(t / 'android-ndk-*'))] + [p for r in _sdk_roots() for p in sorted((r / 'ndk').glob('*'))] + [r / 'ndk-bundle' for r in _sdk_roots()]
    found = [(d, ndk_revision(d)) for d in found if ndk_revision(d)]
    if not found: return None, None
    return next((x for x in found if x[1] == NDK_REVISION), found[-1])


def find_dotnet(cfg=None):
    """The dotnet to use: the configured one as it is; otherwise the first of DOTNET_ROOT, PATH and the usual places that has SDK 10 (a runtime-only dotnet, as Windows
    installs for apps, cannot build nx_extract), else the first found"""
    cfg = cfg if cfg is not None else config.load()
    if cfg.get('dotnet') and Path(cfg['dotnet']).is_file(): return Path(cfg['dotnet'])
    progs = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432')] if os.name == 'nt' else []
    cands = [os.environ.get('DOTNET_ROOT') and str(Path(os.environ['DOTNET_ROOT']) / f'dotnet{EXE}'), shutil.which('dotnet'), str(LOCAL_TOOLS / 'dotnet' / f'dotnet{EXE}'), str(player_tools() / 'dotnet' / f'dotnet{EXE}'),
             str(Path.home() / '.dotnet' / f'dotnet{EXE}'), *[str(Path(p) / 'dotnet' / f'dotnet{EXE}') for p in progs if p], '/usr/local/share/dotnet/dotnet']
    found = list(dict.fromkeys(Path(c) for c in cands if c and Path(c).is_file()))
    return next((d for d in found if any(int(s.split('.')[0]) >= DOTNET_MAJOR for s in dotnet_sdks(d))), found[0] if found else None)


def dotnet_sdks(dotnet):   # {'10.0.402': '/usr/local/share/dotnet/sdk', ...}
    try: r = subprocess.run([str(dotnet), '--list-sdks'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired): return {}
    return dict(re.findall(r'^(\d+\.\d+\.\d+)\S* \[(.+)\]', r.stdout, re.M))


def dotnet_root(dotnet):
    """The folder with dotnet's runtime, where an apphost (nx_extract) looks in DOTNET_ROOT: the SDK folder's parent, not the executable's, which on a Mac is
    a link or wrapper elsewhere (/usr/local/bin from Microsoft's installer, /opt/homebrew/bin)"""
    sdks = dotnet_sdks(dotnet)
    return Path(next(iter(sdks.values()))).parent if sdks else Path(dotnet).resolve().parent


def find_adb(cfg=None):
    cfg = cfg if cfg is not None else config.load()
    for c in [cfg.get('adb'), shutil.which('adb'), *[str(r / 'platform-tools' / f'adb{EXE}') for r in _sdk_roots()], str(LOCAL_TOOLS / 'platform-tools' / f'adb{EXE}'), str(player_tools() / 'platform-tools' / f'adb{EXE}')]:
        if c and Path(c).is_file(): return Path(c)
    return None


def find_jdk(cfg=None):
    """The JDK whose javac compiles the external-screen activity (--external-display, the phone as the pad of a TV): the configured one, JAVA_HOME, a javac on
    PATH, a JDK unpacked beside the NDK, or an installer's folder under Program Files. None without one: only that build needs a JDK"""
    cfg = cfg if cfg is not None else config.load()
    javac = shutil.which('javac')
    cands = [cfg.get('jdk'), os.environ.get('JAVA_HOME'), str(Path(javac).resolve().parent.parent) if javac else None,
             str(LOCAL_TOOLS / 'jdk'), str(player_tools() / 'jdk')]
    if os.name == 'nt':
        progs = [os.environ.get(k) for k in ('ProgramFiles', 'ProgramW6432')]
        cands += [str(d) for p in progs if p for d in sorted(Path(p).glob('jdk*'))]   # jdk-17, jdk-21…
        ndk, _rev = find_ndk(cfg)
        if ndk: cands += [str(d) for d in sorted(ndk.parent.glob('jdk*'))]   # unpacked beside the NDK (the player's tools folder, another drive)
    for c in cands:
        if c and (Path(c) / 'bin' / f'javac{EXE}').is_file(): return Path(c)
    return None


def _importable(m):
    try: return importlib.util.find_spec(m) is not None
    except (ImportError, ValueError): return False


def checks(cfg=None, keys=None):
    """Every requirement, as Check rows (the order the setup screen shows them)"""
    cfg = cfg if cfg is not None else config.load()
    out = []
    v = sys.version_info
    out.append(Check('Python', v >= (3, 11), f'{v.major}.{v.minor}.{v.micro}（{sys.executable}）', fix='需要 Python 3.11 或更高版本'))
    miss = [pip for m, pip in PY_MODULES.items() if not _importable(m)]
    if not (_importable('compression.zstd') or _importable('backports.zstd')): miss.append('backports.zstd')   # zstd: in the standard library from Python 3.14
    out.append(Check('Python 模块', not miss, '缺少：' + ', '.join(miss) if miss else 'lz4, capstone, zstd', fix='pip install ' + ' '.join(miss) if miss else ''))
    out.append(Check('Pillow', _importable('PIL'), '用游戏的图标生成应用图标' if _importable('PIL') else '缺少：应用将没有图标', required=False, fix='pip install pillow'))
    aes = _importable('Crypto.Cipher') or _importable('Cryptodome.Cipher')
    out.append(Check('pycryptodome', aes, 'NSZ / XCZ 转储（porter.dumps）' if aes else '缺少：NSZ / XCZ 转储必须先解压为 NSP / XCI', required=False, fix='' if aes else 'pip install pycryptodome'))
    out.append(Check('fontTools', _importable('fontTools'), '游戏文本中的按键图标' if _importable('fontTools') else '缺少：游戏文本中的按键图标会显示为“?”', required=False, fix='pip install fonttools'))
    ndk, rev = find_ndk(cfg)
    if not ndk: out.append(Check('Android NDK', False, '未找到', fix=f'点击“下载缺失的工具”，或安装 NDK {NDK_RELEASE}（developer.android.com/ndk/downloads）并设置其文件夹'))
    else: out.append(Check('Android NDK', rev == NDK_REVISION, f'{rev}，位于 {ndk}' + ('' if rev == NDK_REVISION else f'：宿主库是用 {NDK_RELEASE}（{NDK_REVISION}）构建的，其他版本未测试'),
                           required=rev.split('.')[0] == NDK_REVISION.split('.')[0], path=str(ndk), fix='' if rev == NDK_REVISION else f'请安装 NDK {NDK_RELEASE}'))
    dn = find_dotnet(cfg)
    sdks = dotnet_sdks(dn) if dn else []
    good = [s for s in sdks if int(s.split('.')[0]) >= DOTNET_MAJOR]
    out.append(Check('.NET SDK', bool(good), (f'{", ".join(sdks) or "无 SDK"}（{dn}）' if dn else '未找到 dotnet'), path=str(dn or ''), fix='' if good else f'点击“下载缺失的工具”，或安装 .NET SDK {DOTNET_MAJOR}（dotnet.microsoft.com）'))
    adb = find_adb(cfg)
    out.append(Check('adb', bool(adb), str(adb) if adb else '未找到：工具无法安装到设备（仍会生成 APK 与数据包）', required=False, path=str(adb or ''),
                     fix='' if adb else '点击“下载缺失的工具”，或安装 Android platform-tools'))
    jdk = find_jdk(cfg)
    out.append(Check('JDK（外接屏）', bool(jdk), str(jdk) if jdk else '未找到：接电视的 --external-display 需要 javac', required=False, path=str(jdk or ''),
                     fix='' if jdk else '安装 JDK 17（adoptium.net），或 porter config jdk <JDK 文件夹>'))
    k = keys or cfg.get('keys')
    out.append(Check('prod.keys', bool(k) and Path(k).is_file(), k if k and Path(k).is_file() else (f'文件不存在：{k}' if k else '未设置'), path=k or '',
                     fix='' if k and Path(k).is_file() else '请从你自己的 Switch 中导出 prod.keys 并设置其路径'))
    where = Path(cfg.get('out') or Path.home())   # the output folder's drive, or the home folder's (a port's build folder goes beside the game files: the port checks that drive itself)
    where = next((p for p in (where, *where.parents) if p.exists()), Path.home())
    free = shutil.disk_usage(where).free / 2**30
    out.append(Check('磁盘空间', free >= DISK_GB, f'{where.anchor} 上可用 {free:.0f} GB', required=False,
                     fix='' if free >= DISK_GB else f'一次移植约占游戏文件的两倍空间（NEO 为 7 GB）；请保持 {DISK_GB} GB 以上可用空间'))
    return out


def require(cfg=None, keys=None, adb=False):
    """The checked tools for a port: {'ndk', 'dotnet', 'adb'}; ToolError naming every missing requirement"""
    rows = checks(cfg, keys)
    bad = [r for r in rows if not r.ok and (r.required or (adb and r.name == 'adb'))]
    if bad: raise ToolError('缺少依赖：' + '；'.join(f'{r.name}：{r.detail}' + (f'（{r.fix}）' if r.fix else '') for r in bad))
    by = {r.name: r for r in rows}
    return {'ndk': Path(by['Android NDK'].path), 'dotnet': Path(by['.NET SDK'].path), 'adb': Path(by['adb'].path) if by['adb'].ok else None}
