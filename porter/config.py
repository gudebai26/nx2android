# The player's settings, kept in <user dir>/config.json: toolchain paths, prod.keys, the output folder, the device. The GUI's setup screen and `porter config` write them;
# command-line options win over them.
import json

from porter.paths import user_dir

KEYS = {
    'keys': 'prod.keys 的路径',
    'firmware': "主机系统内容文件（.nca，即固件转储的 NAND SYSTEM Contents/registered）所在文件夹，供读取系统数据的游戏使用",
    'ndk': 'Android NDK 文件夹（r27c）',
    'dotnet': 'dotnet 可执行文件（.NET SDK 10）',
    'adb': 'adb 可执行文件（platform-tools）',
    'jdk': 'JDK 17 文件夹（仅接电视的 --external-display 需要：用它编译手机手柄界面）',
    'out': 'APK 与数据包的输出文件夹（留空即游戏文件夹）',
    'library': '已移植游戏的文件夹（“推送”页面扫描它，留空即当前目录）',
    'device': '要安装到的设备的 adb 序列号（留空即唯一连接的设备）',
    'language': '游戏语言代码（留空即英语）',
}


def path():
    return user_dir() / 'config.json'


def load():
    try: c = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError): c = {}
    return {k: c.get(k, '') for k in KEYS}


def save(c):
    p = path(); tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({k: c.get(k, '') for k in KEYS}, indent=2), encoding='utf-8'); tmp.replace(p)


def set_value(key, value):
    if key not in KEYS: raise KeyError(f'未知的设置项 {key!r}：可选 {", ".join(KEYS)}')
    c = load(); c[key] = value; save(c)
