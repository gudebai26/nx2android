# The nx2android desktop app: five tabs over porter.
#   Port   pick the game folder (porter inspect), options, Port (porter port --progress json): the stage list, the current stage's bar, the log; then open or install the result
#   Push   ports already built on this computer (porter library) to a connected device (porter install): the folder, the game, the device
#   Setup  the tools a port needs (porter doctor), downloading the missing ones (porter tools --download), and the saved settings (porter.config): prod.keys, the NDK,
#          dotnet, adb, the output folder, the device
#   Games  the game profiles (porter profiles --all)
#   Saves  a ported game's save between the device and the PC (porter saves): the save folders on the device, a fresh export to a folder here, an import of a
#          folder or .zip (a console's save, or an export)
import os, subprocess, sys, time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QStyle, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from porter import config, library
from gui.runner import Porter, call_json

LANGUAGES = [('', '英语（默认）'), ('ja', '日语'), ('fr', '法语'), ('de', '德语'), ('it', '意大利语'), ('es', '西班牙语'), ('nl', '荷兰语'), ('pt', '葡萄牙语'),
             ('ru', '俄语'), ('ko', '韩语'), ('zh-Hans', '简体中文'), ('zh-Hant', '繁体中文')]
STATUS_COLOUR = {'playable': '#2e7d32', 'wip': '#ef6c00', 'boots': '#ef6c00', 'experimental': '#ef6c00', 'ios only': '#757575'}
# the profile statuses the tables show (porter/profiles.py), in Chinese; the colour map above stays keyed by the profile's own words
STATUS_TEXT = {'playable': '可玩', 'wip': '开发中', 'boots': '可启动', 'experimental': '实验性', 'ios only': '仅 iOS', 'not supported': '不支持'}


def status_text(st): return STATUS_TEXT.get(st, st)


def hms(s):
    s = int(s or 0); return f'{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}' if s >= 3600 else f'{s // 60}:{s % 60:02d}'


def size(b):
    for u, d in (('GB', 2**30), ('MB', 2**20), ('KB', 2**10)):
        if b >= d: return f'{b / d:.1f} {u}'
    return f'{b} B'


def path_row(edit, pick):   # a line edit with a Browse button
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    b = QPushButton('浏览…'); b.clicked.connect(pick)
    h.addWidget(edit, 1); h.addWidget(b)
    return w


def with_info(field, tip):   # a field with the style's information icon after it, which shows tip on hover (the field shows it too)
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0)
    i = QLabel(); i.setPixmap(QApplication.style().standardIcon(QStyle.SP_MessageBoxInformation).pixmap(16, 16)); i.setCursor(Qt.WhatsThisCursor)
    i.setToolTip(tip); field.setToolTip(tip)
    h.addWidget(field); h.addWidget(i); h.addStretch(1)
    return w


# ---------------- Port ----------------
class PortTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win, self.info, self.result, self.items, self.t0 = win, None, None, {}, 0
        self.porter = Porter(self); self.porter.event.connect(self.on_event); self.porter.finished.connect(self.on_finished)
        v = QVBoxLayout(self)

        self.folder = QLineEdit(); self.folder.setPlaceholderText('存放游戏 NSP / XCI / NSZ / XCZ 文件的文件夹（本体、更新、DLC）')
        self.folder.editingFinished.connect(self.inspect)
        g = QGroupBox('游戏'); gl = QVBoxLayout(g)
        gl.addWidget(path_row(self.folder, self.pick_folder))
        self.card = QLabel('请选择游戏文件夹。'); self.card.setWordWrap(True); self.card.setTextFormat(Qt.RichText); self.card.setMinimumHeight(60)
        gl.addWidget(self.card)
        v.addWidget(g)

        o = QGroupBox('选项'); f = QFormLayout(o)
        self.app_name = QLineEdit(); self.app_name.setPlaceholderText('游戏名称')
        self.language = QComboBox()
        for code, name in LANGUAGES: self.language.addItem(name, code)
        self.out = QLineEdit(); self.out.setPlaceholderText('游戏文件夹')
        self.install = QCheckBox('完成后安装到设备')
        self.device = QComboBox(); self.device.setMinimumWidth(260)
        refresh = QPushButton('刷新'); refresh.clicked.connect(self.refresh_devices)
        dev = QWidget(); dh = QHBoxLayout(dev); dh.setContentsMargins(0, 0, 0, 0); dh.addWidget(self.install); dh.addWidget(self.device, 1); dh.addWidget(refresh)
        self.replace_signer = QCheckBox('替换同包名但由其他密钥签名的应用（会先卸载它：其存档将被删除）')
        self.romfs_at = QLineEdit(); self.romfs_at.setPlaceholderText('按设备现状（早期安装留下的文件夹，否则为应用的 files 文件夹）')   # porter install --romfs-at
        romfs_tip = ('设备上已存放该游戏版本 RomFS 的文件夹（例如模拟器读取的转储，如 /sdcard/<dump>/romfs），\n'
                     '应用将直接读取它，而不再在自己的 files 文件夹中保留副本（可省 7 至 18 GB）。安装程序会检查该文件夹，并授予应用“所有文件访问权限”。\n'
                     '留空：按设备现状处理。“app”：把 RomFS 放回应用的 files 文件夹。仅安装时生效：移植过程本身不变。')
        self.romfs_push = QCheckBox('把游戏的 RomFS 推送到该文件夹（空文件夹，或本游戏的文件夹：只添加文件，绝不删除）')   # porter install --romfs-push
        romfs = with_info(self.romfs_at, romfs_tip); romfs.layout().setStretch(0, 1)
        self.package = QLineEdit(); self.package.setPlaceholderText('配置文件中的包名')   # porter port --package: a test install beside the player's other build
        self.package.setToolTip('用配置文件之外的包名安装（org.nx2a.<game>.test），可与游戏的另一个版本并存。\n'
                                '其他包名的应用拥有独立的存档。')
        self.fps60 = QCheckBox('60 fps（优先使用配置自带的帧率上限，否则使用社区 FPSLocker-Warehouse 中该游戏版本的配置，从 GitHub 获取）')
        self.touch = QCheckBox('触屏游戏：启动时隐藏屏幕手柄，除菜单按钮外的所有触摸都交给游戏（暂停菜单可再次显示）')   # porter port --touch
        self.driver = QLineEdit(); self.driver.setPlaceholderText('设备自带驱动')   # porter port --vulkan-driver
        self.driver.setToolTip('打包进 APK、用来替代设备自带驱动的 Vulkan 驱动库（.so），\n'
                               '例如适用于 Adreno GPU 的 Mesa Turnip。留空：使用设备驱动。\n'
                               '若该库在设备上加载失败，应用会退回使用设备驱动。')
        self.shared_font = QLineEdit(); self.shared_font.setPlaceholderText('设备自带的 Roboto')   # porter port --shared-font
        self.shared_font.setToolTip('作为游戏所请求的系统字体打包的 TrueType 字体（.ttf）。\n'
                                    '包含中日韩字符的字体可以正常显示游戏通过系统字体绘制的中文与韩文。')
        self.mt_replay = QCheckBox('多线程回放：在独立线程上录制游戏的 Vulkan 命令（繁忙场景更快；需重新移植才能关闭）')   # porter port --mt-replay
        self.external = QCheckBox('接电视：生成外接屏界面（手机当手柄、画面输出到电视；需要 JDK 17 与 Android SDK 的 platform/build-tools）')   # porter port --external-display
        self.debug = QCheckBox('开发版本（可调试、包含调试信息、支持 DM_CTL 按键）')
        self.experimental = QCheckBox('实验性：尝试移植尚未支持 Android 的游戏（可能无法启动）')
        self.experimental.toggled.connect(lambda _: self.update_port_button())
        self.vercode = QSpinBox(); self.vercode.setRange(0, 65535); self.vercode.setSpecialValueText('无'); self.vercode.setMaximumWidth(120)   # porter port --vercode-offset (0: none, or $NX2A_VERCODE_OFFSET)
        self.vercode.setToolTip('加到 APK 的 versionCode 上。Android 不会用 versionCode 更低的 APK 覆盖安装；\n'
                                '提高此值可在带偏移的旧构建之上安装同一游戏版本的重建包，并保留其存档。')
        self.headroom = QSpinBox(); self.headroom.setRange(-1, 48); self.headroom.setSpecialValueText('自动'); self.headroom.setValue(-1); self.headroom.setSuffix(' GB')
        self.headroom.setMaximumWidth(120)   # porter port --alias-headroom (Auto: none passed)
        headroom = with_info(self.headroom, '为按地址区域申请内存的游戏增加额外地址空间（别名区域）。\n'
                             '仅用于崩溃且日志中出现“AllocateAddressRegion refused (0x1803)”的游戏。\n'
                             '自动：配置文件未测试过的游戏版本为 8 GB，测试过的版本为 0。\n'
                             '若游戏仍然内存不足，可继续调大（16、24……）。它不占用实际内存，也不影响速度。')
        f.addRow('应用名称', self.app_name); f.addRow('游戏语言', self.language)
        f.addRow('输出文件夹', path_row(self.out, self.pick_out)); f.addRow('设备', dev); f.addRow('', self.replace_signer)
        f.addRow('设备上的 RomFS', romfs); f.addRow('', self.romfs_push)
        f.addRow('包名', self.package); f.addRow('versionCode 偏移', self.vercode); f.addRow('额外地址空间', headroom)
        f.addRow('Vulkan 驱动', path_row(self.driver, self.pick_driver))
        f.addRow('系统字体', path_row(self.shared_font, self.pick_shared_font))
        f.addRow('', self.fps60); f.addRow('', self.touch); f.addRow('', self.mt_replay); f.addRow('', self.external); f.addRow('', self.debug); f.addRow('', self.experimental)
        v.addWidget(o)

        bar = QHBoxLayout()
        self.port_btn = QPushButton('开始移植'); self.port_btn.setEnabled(False); self.port_btn.setMinimumHeight(36)
        f2 = self.port_btn.font(); f2.setPointSize(f2.pointSize() + 2); f2.setBold(True); self.port_btn.setFont(f2)
        self.port_btn.clicked.connect(self.start)
        self.cancel_btn = QPushButton('取消'); self.cancel_btn.setEnabled(False); self.cancel_btn.clicked.connect(self.cancel)
        self.open_btn = QPushButton('打开输出文件夹'); self.open_btn.setEnabled(False); self.open_btn.clicked.connect(self.open_out)
        self.inst_btn = QPushButton('安装到设备'); self.inst_btn.setEnabled(False); self.inst_btn.clicked.connect(self.install_now)
        for b in (self.port_btn, self.cancel_btn): bar.addWidget(b)
        bar.addStretch(1)
        for b in (self.open_btn, self.inst_btn): bar.addWidget(b)
        v.addLayout(bar)

        self.step = QLabel(''); self.prog = QProgressBar(); self.prog.setTextVisible(True); self.prog.setRange(0, 1000); self.prog.setValue(0)
        v.addWidget(self.step); v.addWidget(self.prog)
        split = QSplitter(Qt.Vertical)
        self.stages = QTreeWidget(); self.stages.setHeaderLabels(['', '阶段', '结果', '耗时']); self.stages.setRootIsDecorated(False)
        hh = self.stages.header(); hh.setSectionResizeMode(0, QHeaderView.ResizeToContents); hh.setSectionResizeMode(1, QHeaderView.ResizeToContents); hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setFont(QFont('Consolas' if os.name == 'nt' else 'Menlo', 9)); self.log.setMaximumBlockCount(20000)
        split.addWidget(self.stages); split.addWidget(self.log); split.setSizes([180, 220])
        v.addWidget(split, 1)
        self.tick = QTimer(self); self.tick.timeout.connect(self.on_tick); self.tick.start(1000)
        self.refresh_devices()

    # ---- the game folder ----
    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, '游戏文件夹', self.folder.text() or str(Path.home()))
        if d: self.folder.setText(d); self.inspect()

    def pick_driver(self):
        p = QFileDialog.getOpenFileName(self, 'Vulkan 驱动', self.driver.text() or str(Path.home()), 'Vulkan 驱动 (*.so)')[0]
        if p: self.driver.setText(p)

    def pick_shared_font(self):
        p = QFileDialog.getOpenFileName(self, '系统字体', self.shared_font.text() or str(Path.home()), 'TrueType 字体 (*.ttf)')[0]
        if p: self.shared_font.setText(p)

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, '输出文件夹', self.out.text() or self.folder.text() or str(Path.home()))
        if d: self.out.setText(d)

    def inspect(self):
        d = self.folder.text().strip()
        self.info = None; self.port_btn.setEnabled(False)
        if not d: return
        if not Path(d).is_dir(): self.card.setText(f'<span style="color:#c62828">不是文件夹：{d}</span>'); return
        self.card.setText('正在读取游戏文件夹…')
        call_json(self, ['inspect', d, '--json'], self.on_inspect)

    def on_inspect(self, info, code):
        err = info
        self.info = info = None if info and info.get('error') else info
        if not info: self.card.setText('<span style="color:#c62828">' + ((err or {}).get('error') or '此文件夹中未找到游戏（需要带有 title ID 的 NSP、XCI、NSZ 或 XCZ 文件）。') + '</span>'); return
        a = info.get('android') or {}
        st = a.get('status', 'ios only' if info.get('profile') else 'not supported')
        col = STATUS_COLOUR.get(st, '#c62828')
        rows = [(f'<b>{info["name"]}</b> &nbsp; <span style="color:#757575">{info["title_id"]}</span>' if info.get('name') else f'<b>{info["title_id"]}</b>') + ' &nbsp; '
                f'<span style="background:{col};color:white;padding:1px 6px">&nbsp;{status_text(st)}&nbsp;</span>']
        files = [f'本体'] + [f'更新 {u["version"]}' for u in info['updates']] + ([f'{len(info["dlc"])} 个 DLC'] if info['dlc'] else [])
        rows.append('文件夹内容：' + '、'.join(files) + (f'；包含补丁包 <b>{info["pack"]}</b>' if info.get('pack') else ''))
        if info.get('compressed'): rows.append(f'<span style="color:#757575">NSZ / XCZ（{len(info["compressed"])}）：移植时解压到临时文件夹，解压后删除</span>')
        if a.get('tested'): rows.append(f'测试情况：{a["tested"]}')
        if info.get('reason'): rows.append(f'<span style="color:{"#ef6c00" if info["supported"] else "#c62828"}">{info["reason"]}</span>')
        self.card.setText('<br>'.join(rows))
        self.app_name.setPlaceholderText(info.get('name') or '游戏名称')
        self.package.setPlaceholderText(a.get('package') or '配置文件中的包名')
        self.update_port_button()

    def update_port_button(self):   # a supported game, or any game with Experimental ticked
        i = self.info or {}
        self.port_btn.setEnabled(bool(i.get('supported') or (i.get('experimental') and self.experimental.isChecked())) and not self.porter.running())

    def refresh_devices(self):
        cur = self.device.currentData() or config.load().get('device')
        def got(rows, code):
            self.device.clear()
            for r in rows or []:
                if r['state'] == 'device': self.device.addItem(f'{r["serial"]}  {r["info"]}', r['serial'])
            if not self.device.count(): self.device.addItem('未连接设备', '')
            i = self.device.findData(cur)
            if i >= 0: self.device.setCurrentIndex(i)
        call_json(self, ['devices', '--json'], got)

    # ---- the port ----
    def start(self):
        cfg = config.load()
        if not cfg.get('keys'):
            QMessageBox.warning(self, 'prod.keys', '请先在“设置”标签页中设置 prod.keys 的路径。'); self.win.tabs.setCurrentIndex(1); return
        args = ['port', self.folder.text().strip(), '--progress', 'json']
        if self.app_name.text().strip(): args += ['--app-name', self.app_name.text().strip()]
        if self.language.currentData(): args += ['--language', self.language.currentData()]
        if self.out.text().strip(): args += ['--out', self.out.text().strip()]
        if self.install.isChecked():
            args.append('--install')
            if self.device.currentData(): args += ['--device', self.device.currentData()]
            if self.replace_signer.isChecked(): args.append('--replace-signer')
            args += self.romfs_args()
        if self.package.text().strip(): args += ['--package', self.package.text().strip()]
        if self.vercode.value(): args += ['--vercode-offset', str(self.vercode.value())]
        if self.headroom.value() >= 0: args += ['--alias-headroom', str(self.headroom.value())]
        if self.fps60.isChecked(): args.append('--fps60')
        if self.touch.isChecked(): args.append('--touch')
        if self.mt_replay.isChecked(): args.append('--mt-replay')
        if self.external.isChecked(): args.append('--external-display')
        if self.driver.text().strip(): args += ['--vulkan-driver', self.driver.text().strip()]
        if self.shared_font.text().strip(): args += ['--shared-font', self.shared_font.text().strip()]
        if self.debug.isChecked(): args.append('--debug')
        if self.experimental.isChecked() and not self.info.get('supported'): args.append('--experimental')
        self.run(args)

    def install_now(self):
        if not self.result: return
        args = ['install', self.result['data'], '--apk', self.result['apk'], '--progress', 'json']
        if self.device.currentData(): args += ['--device', self.device.currentData()]
        if self.replace_signer.isChecked(): args.append('--replace-signer')
        self.run(args + self.romfs_args())

    def romfs_args(self):   # porter install --romfs-at / --romfs-push (porter/install.py)
        return (['--romfs-at', self.romfs_at.text().strip()] if self.romfs_at.text().strip() else []) + (['--romfs-push'] if self.romfs_push.isChecked() else [])

    def run(self, args):
        self.stages.clear(); self.items = {}; self.log.clear(); self.prog.setValue(0); self.step.setText('正在启动…')
        self.result = None; self.t0 = time.time(); self.cancelled = False
        for b in (self.port_btn, self.open_btn, self.inst_btn): b.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.append('$ porter ' + ' '.join(f'"{a}"' if ' ' in a else a for a in args))
        self.porter.start(args)

    def cancel(self):
        self.cancelled = True; self.porter.cancel(); self.append('[RED] 已取消')

    def append(self, msg):
        red = msg.startswith('[RED] ')
        self.log.appendHtml(f'<span style="color:{"#c62828" if red else "inherit"}">{msg.removeprefix("[RED] ").replace("&", "&amp;").replace("<", "&lt;").replace(chr(10), "<br>")}</span>')

    def item(self, k, name):
        it = self.items.get(k)
        if not it:
            it = QTreeWidgetItem(['…', name, '', '']); self.stages.addTopLevelItem(it); self.items[k] = it
        return it

    def on_event(self, ev):
        e = ev.get('ev')
        if e == 'stage':
            self.cur = ev; it = self.item(ev['k'], ev['name']); it.setText(0, '▶'); it.setData(0, Qt.UserRole, time.time())
            self.step.setText(f'[{ev["k"]}/{ev["n"]}] {ev["name"]}' + (f' · {ev["step"]}' if ev.get('step') else '')); self.prog.setRange(0, 0)
        elif e == 'step':
            self.step.setText(f'{ev["name"]} · {ev["step"]}'); self.prog.setRange(0, 0)
        elif e == 'progress':
            done, total = ev.get('done'), ev.get('total')
            count = '' if done is None else (f'{size(done)} / {size(total)}' if ev.get('unit') == 'B' and total else f'{done:,}' + (f' / {total:,}' if total else '') + (f' {ev["unit"]}' if ev.get('unit') else ''))
            self.step.setText(f'{ev["name"]}' + (f' · {ev["step"]}' if ev.get('step') else '') + (f' · {count}' if count else '') + (f' · {ev["note"]}' if ev.get('note') else '')
                              + (f' · 预计剩余 {hms(ev["eta"])}' if ev.get('eta') else ''))
            if ev.get('frac') is not None: self.prog.setRange(0, 1000); self.prog.setValue(int(ev['frac'] * 1000))
            else: self.prog.setRange(0, 0)
        elif e == 'stage_end':
            it = self.item(ev['k'], ev['name']); ok = ev.get('ok', True)
            it.setText(0, '✓' if ok else '✗'); it.setForeground(0, QColor('#2e7d32' if ok else '#c62828'))
            it.setText(2, ev.get('summary', '')); it.setText(3, hms(ev.get('elapsed')))
            self.prog.setRange(0, 1000); self.prog.setValue(1000 if ok else 0)
        elif e == 'say':
            self.append(ev.get('msg', ''))
        elif e == 'result':
            self.result = ev

    def on_tick(self):   # the running stage's elapsed time
        if not self.porter.running(): return
        for it in self.items.values():
            t = it.data(0, Qt.UserRole)
            if it.text(0) == '▶' and t: it.setText(3, hms(time.time() - t))

    def on_finished(self, code):
        self.cancel_btn.setEnabled(False)
        self.update_port_button()
        took = hms(time.time() - self.t0)
        if code == 0:
            self.step.setText(f'完成，用时 {took}'); self.prog.setRange(0, 1000); self.prog.setValue(1000)
            if self.result: self.open_btn.setEnabled(True); self.inst_btn.setEnabled(True); self.win.push.adopt(self.result)
        else:
            self.step.setText('已取消' if self.cancelled or code == 130 else f'失败，用时 {took}（原因见日志）')
            self.prog.setRange(0, 1000); self.prog.setValue(0)

    def open_out(self):
        if self.result: QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result['apk']).parent)))


# ---------------- Setup ----------------
class SetupTab(QWidget):
    FIELDS = [('keys', 'prod.keys', 'file'), ('ndk', 'Android NDK 文件夹', 'dir'), ('dotnet', 'dotnet', 'file'), ('adb', 'adb', 'file'),
              ('jdk', 'JDK 文件夹（接电视）', 'dir'), ('out', '默认输出文件夹', 'dir'), ('device', '设备（adb 序列号）', 'text')]

    def __init__(self, win):
        super().__init__()
        self.win = win
        v = QVBoxLayout(self)
        g = QGroupBox('依赖检查'); gl = QVBoxLayout(g)
        self.table = QTableWidget(0, 3); self.table.setHorizontalHeaderLabels(['', '项目', '状态']); self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setMinimumHeight(320)   # 表头 + 十一项检查，无需滚动
        hh = self.table.horizontalHeader(); hh.setSectionResizeMode(0, QHeaderView.ResizeToContents); hh.setSectionResizeMode(1, QHeaderView.ResizeToContents); hh.setSectionResizeMode(2, QHeaderView.Stretch)
        gl.addWidget(self.table)
        note = QLabel('移植需要 <b>Android NDK r27c</b>、<b>.NET SDK 10</b>，若要从这里安装到设备还需要 Android <b>platform-tools</b>；接电视玩（外接屏）还需要 <b>JDK 17</b> 与 Android SDK 的 platform / build-tools。'
                      '“<b>下载缺失的工具</b>”会从 Google 与 Microsoft 获取它们（约 1 GB）并放入你的 nx2android 文件夹；你也可以自行安装，并在下方填写路径。'
                      '你的 <b>prod.keys</b> 必须来自你自己的 Switch。密钥与游戏文件绝不会离开你的电脑，只会传到你自己的设备。')
        note.setWordWrap(True); note.setTextFormat(Qt.RichText); gl.addWidget(note)
        dl = QHBoxLayout(); self.dl_btn = QPushButton('下载缺失的工具'); self.dl_btn.clicked.connect(self.download)
        self.dl_step = QLabel(''); self.dl_prog = QProgressBar(); self.dl_prog.setRange(0, 1000); self.dl_prog.setVisible(False)
        dl.addWidget(self.dl_btn); dl.addWidget(self.dl_step, 1); dl.addWidget(self.dl_prog, 1); gl.addLayout(dl)
        self.fetch = Porter(self); self.fetch.event.connect(self.on_fetch); self.fetch.finished.connect(self.fetched)
        v.addWidget(g)
        s = QGroupBox('设置'); f = QFormLayout(s); self.edits = {}
        for key, label, kind in self.FIELDS:
            e = QLineEdit(); self.edits[key] = e
            e.setPlaceholderText({'ndk': '自动查找', 'dotnet': '自动查找', 'adb': '自动查找', 'jdk': '自动查找', 'out': '游戏文件夹', 'device': '唯一连接的设备'}.get(key, ''))
            f.addRow(label, e if kind == 'text' else path_row(e, lambda _=False, k=key, kd=kind: self.pick(k, kd)))
        row = QHBoxLayout(); save = QPushButton('保存并检查'); save.clicked.connect(self.save); row.addStretch(1); row.addWidget(save)
        f.addRow('', row)
        v.addWidget(s); v.addStretch(1)
        self.load()

    # ---- downloading the missing tools ----
    def download(self):
        self.dl_btn.setEnabled(False); self.dl_step.setText('正在读取下载列表…')
        call_json(self, ['tools', '--json'], self.confirm_download)

    def confirm_download(self, items, code):
        self.dl_btn.setEnabled(True); self.dl_step.setText('')
        if items is None: QMessageBox.warning(self, '下载', '无法从 Google 与 Microsoft 读取下载列表。请检查网络连接后重试。'); return
        if not items: QMessageBox.information(self, '下载', '无需下载：所有工具都已就绪。'); return
        rows = ''.join(f'<li><b>{it["name"]}</b> {it["version"]}，{size(it["size"])}，来源 {it["url"].split("/")[2]}（<a href="{it["license"]}">许可证</a>）</li>' for it in items)
        box = QMessageBox(QMessageBox.Question, '下载缺失的工具', f'<p>将下载并解压到你的 nx2android 文件夹：</p><ul>{rows}</ul>'
                          '<p>它们由各自的开发者按各自的许可证提供：下载即表示你接受这些许可证。</p>', QMessageBox.Yes | QMessageBox.No, self)
        box.setTextFormat(Qt.RichText)
        if box.exec() != QMessageBox.Yes: return
        self.dl_btn.setEnabled(False); self.dl_prog.setVisible(True); self.dl_prog.setRange(0, 0); self.dl_errors = []
        self.fetch.start(['tools', '--download', '--progress', 'json'])

    def on_fetch(self, ev):
        e = ev.get('ev')
        if e == 'stage': self.dl_step.setText(f'[{ev["k"]}/{ev["n"]}] {ev["name"]}'); self.dl_prog.setRange(0, 0)
        elif e == 'progress':
            done, total = ev.get('done'), ev.get('total')
            self.dl_step.setText(f'{ev["name"]} · {ev.get("step") or ""}' + (f' · {size(done)} / {size(total)}' if ev.get('unit') == 'B' and total and done is not None else ''))
            if ev.get('frac') is not None: self.dl_prog.setRange(0, 1000); self.dl_prog.setValue(int(ev['frac'] * 1000))
            else: self.dl_prog.setRange(0, 0)
        elif e == 'say' and ev.get('msg', '').startswith('[RED] '): self.dl_errors.append(ev['msg'].removeprefix('[RED] '))

    def fetched(self, code):
        self.dl_btn.setEnabled(True); self.dl_prog.setVisible(False)
        self.dl_step.setText('工具已就绪' if code == 0 else '下载失败')
        if code: QMessageBox.warning(self, '下载', '\n'.join(self.dl_errors) or '下载失败。')
        self.load()

    def pick(self, key, kind):
        e = self.edits[key]
        p = QFileDialog.getExistingDirectory(self, key, e.text()) if kind == 'dir' else QFileDialog.getOpenFileName(self, key, e.text())[0]
        if p: e.setText(p)

    def load(self):
        for k, v in config.load().items():
            if k in self.edits: self.edits[k].setText(v)
        self.check()

    def save(self):
        c = config.load()
        for k, e in self.edits.items(): c[k] = e.text().strip()
        config.save(c); self.check()

    def check(self):
        def got(rows, code):
            self.table.setRowCount(0)
            for r in rows or []:
                i = self.table.rowCount(); self.table.insertRow(i)
                mark = QTableWidgetItem('✓' if r['ok'] else ('✗' if r['required'] else '–'))
                mark.setForeground(QColor('#2e7d32' if r['ok'] else '#c62828' if r['required'] else '#ef6c00'))
                self.table.setItem(i, 0, mark); self.table.setItem(i, 1, QTableWidgetItem(r['name']))
                self.table.setItem(i, 2, QTableWidgetItem(r['detail'] + (f'  →  {r["fix"]}' if r['fix'] and not r['ok'] else '')))
            self.table.resizeRowsToContents()
            self.win.set_ready(bool(rows) and all(r['ok'] or not r['required'] for r in rows))
        call_json(self, ['doctor', '--json'], got)


# ---------------- Games ----------------
class GamesTab(QWidget):
    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(['状态', '游戏', 'Title ID', '测试设备'])
        self.table.verticalHeader().setVisible(False); self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader(); hh.setSectionResizeMode(1, QHeaderView.Stretch); hh.setSectionResizeMode(2, QHeaderView.ResizeToContents); hh.setSectionResizeMode(3, QHeaderView.Stretch)
        lab = QLabel('已移植到 Android 的游戏。<b>可玩</b>、<b>开发中</b>（可玩但部分内容缺失）与 <b>可启动</b> 的游戏均可移植；其他游戏仍可在“移植”标签页勾选“实验性”尝试。')
        lab.setWordWrap(True); lab.setTextFormat(Qt.RichText)
        v.addWidget(lab); v.addWidget(self.table)
        call_json(self, ['profiles', '--json'], self.got)

    def got(self, rows, code):
        rows = sorted(rows or [], key=lambda r: r['name'].lower())
        self.table.setSortingEnabled(False); self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            st = QTableWidgetItem(status_text(r['status'])); st.setForeground(QColor(STATUS_COLOUR.get(r['status'], '#000'))); st.setData(Qt.UserRole, r['status'])
            for j, it in enumerate((st, QTableWidgetItem(r['name']), QTableWidgetItem(r['id']), QTableWidgetItem(r['tested']))): self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)


# ---------------- Saves ----------------
class SavesTab(QWidget):
    def __init__(self):
        super().__init__()
        self.busy = None
        v = QVBoxLayout(self)
        lab = QLabel('在设备与这台电脑之间转移已移植游戏的存档。应用把导出的存档保存在设备上自己的 files 文件夹中（<code>Android/data/&lt;package&gt;/files/saves</code>），'
                     '每个存档一个文件夹，文件保持原样：与主机的存档工具所复制的结构相同，因此主机的存档文件夹可以直接导入。'
                     '游戏的暂停菜单中也有“导出存档”和“导入存档”。在此导出或导入会先停止设备上的应用；导入前会先导出将被替换的存档。')
        lab.setWordWrap(True); lab.setTextFormat(Qt.RichText); v.addWidget(lab)
        row = QHBoxLayout(); self.app = QComboBox(); self.app.currentIndexChanged.connect(self.refresh)
        r = QPushButton('刷新'); r.clicked.connect(self.load_apps)
        row.addWidget(QLabel('设备上的游戏：')); row.addWidget(self.app, 1); row.addWidget(r); v.addLayout(row)
        self.table = QTableWidget(0, 4); self.table.setHorizontalHeaderLabels(['存档文件夹', '时间', '文件数', '大小'])
        self.table.verticalHeader().setVisible(False); self.table.setEditTriggers(QTableWidget.NoEditTriggers); self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection); self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        b = QHBoxLayout()
        self.b_export = QPushButton('导出存档到电脑…'); self.b_export.clicked.connect(self.export_new)
        self.b_copy = QPushButton('复制所选文件夹到电脑…'); self.b_copy.clicked.connect(self.export_chosen)
        self.b_import = QPushButton('导入存档文件夹…'); self.b_import.clicked.connect(lambda: self.import_(False))
        self.b_zip = QPushButton('导入 .zip…'); self.b_zip.clicked.connect(lambda: self.import_(True))
        for w in (self.b_export, self.b_copy, self.b_import, self.b_zip): b.addWidget(w)
        v.addLayout(b)
        self.status = QLabel(''); self.status.setWordWrap(True); v.addWidget(self.status)
        self.load_apps()

    def pkg(self): return self.app.currentData() or ''

    def set_status(self, msg, red=False):   # 状态行：错误显示为红色，不再带 [RED] 标记
        self.status.setText(msg); self.status.setStyleSheet('color:#c62828' if red else '')

    def set_busy(self, msg, show=True):   # msg: what runs (None: nothing); its line goes when it ends, a result written over it stays
        if msg and show: self.set_status(msg)
        elif not msg and self.busy and self.status.text() == self.busy: self.set_status('')
        self.busy = msg
        for w in (self.b_export, self.b_copy, self.b_import, self.b_zip, self.app): w.setEnabled(not msg and (w is self.app or bool(self.pkg())))

    def load_apps(self):
        def got(rows, code):
            cur = self.pkg(); self.app.blockSignals(True); self.app.clear()
            for pk in rows if isinstance(rows, list) else []: self.app.addItem(pk, pk)
            if not self.app.count(): self.app.addItem('设备上没有已移植的游戏', '')
            i = self.app.findData(cur); self.app.setCurrentIndex(max(i, 0)); self.app.blockSignals(False)
            self.set_busy(None); self.refresh()
            if not isinstance(rows, list): self.set_status((rows or {}).get("error", "porter saves apps 执行失败"), red=True)
        self.set_busy('正在查找设备…'); call_json(self, ['saves', 'apps', '--json'], got)

    def refresh(self, show=True):
        self.table.setRowCount(0)
        if not self.pkg(): return
        def got(rows, code):
            self.set_busy(None)
            if not isinstance(rows, list): self.set_status((rows or {}).get("error", "porter saves list 执行失败"), red=True); return
            self.table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                cells = (r['name'] + ('  （+ 设备存档）' if r['device'] else ''), time.strftime('%Y-%m-%d %H:%M', time.localtime(r['time'])), str(r['files']), size(r['bytes']))
                for j, c in enumerate(cells):
                    it = QTableWidgetItem(c); it.setData(Qt.UserRole, r['name']); self.table.setItem(i, j, it)
            if not rows: self.set_status('设备上还没有存档文件夹：导出一个（在此处，或在游戏的暂停菜单中）。')
        self.set_busy('正在读取存档文件夹…', show); call_json(self, ['saves', 'list', '--package', self.pkg(), '--json'], got)

    def done(self, what):
        def got(res, code):
            self.set_busy(None)
            if code or not isinstance(res, dict) or res.get('error'): self.set_status(f'{what}失败：{(res or {}).get("error", "原因见 porter saves")}', red=True)
            elif 'folder' in res: self.set_status(f'已导出到 {res["folder"]}'); QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(res['folder']).parent)))
            else: self.set_status(f'已导入 {res.get("name")}' + (f'；被替换的存档在设备上是 {res["backup"]}' if res.get('backup') else '') + '。请在设备上打开游戏。')
            if not code: self.refresh(show=False)
        return got

    def export_new(self):
        d = QFileDialog.getExistingDirectory(self, '存放存档的文件夹')
        if not d: return
        self.set_busy(f'正在导出 {self.pkg()} 的存档（会在设备上启动应用以复制存档）…')
        call_json(self, ['saves', 'export', d, '--package', self.pkg(), '--json'], self.done('导出'))

    def export_chosen(self):
        it = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        if not it: self.set_status('请先在列表中选择一个存档文件夹。'); return
        d = QFileDialog.getExistingDirectory(self, '存放存档的文件夹')
        if not d: return
        self.set_busy(f'正在复制 {it.data(Qt.UserRole)}…')
        call_json(self, ['saves', 'export', d, '--name', it.data(Qt.UserRole), '--package', self.pkg(), '--json'], self.done('复制'))

    def import_(self, zipped):
        src = QFileDialog.getOpenFileName(self, '存档 .zip 文件', '', 'Zip 压缩包 (*.zip)')[0] if zipped else QFileDialog.getExistingDirectory(self, '存档文件夹（其文件位于最上层）')
        if not src: return
        if QMessageBox.question(self, '导入存档', f'{Path(src).name} 将作为 {self.pkg()} 在设备上的存档。应用会被停止；被替换的存档会先在设备上导出'
                                '（文件夹名以 -before-import 结尾）。继续吗？') != QMessageBox.Yes: return
        self.set_busy(f'正在导入 {Path(src).name}…')
        call_json(self, ['saves', 'import', src, '--package', self.pkg(), '--json'], self.done('导入'))


# ---------------- Push ----------------
class PushTab(QWidget):
    """Ports already built on this computer → the phone: the folder that holds them (or one port), the game, the device (the only one when
    there is one), then porter install. The device's own files are compared first, so a game already there costs seconds, not 15 GB."""
    COLS = ['游戏', '包名', '版本', '大小', '设备上']

    def __init__(self, win):
        super().__init__()
        self.win, self.rows, self.installed, self.t0 = win, [], set(), 0
        self.porter = Porter(self); self.porter.event.connect(self.on_event); self.porter.finished.connect(self.on_finished)
        v = QVBoxLayout(self)
        lab = QLabel('推送已经移植好的游戏：选择存放移植结果的文件夹（也可以直接选某个游戏的数据包），下面会列出其中的每个数据包与同名 APK。'
                     '选一个游戏和设备，点“推送到设备”。设备上已经有的文件会跳过，换版本时只补差异。')
        lab.setWordWrap(True); lab.setTextFormat(Qt.RichText); v.addWidget(lab)
        row = QHBoxLayout(); self.folder = QLineEdit(); self.folder.setPlaceholderText('已移植游戏的文件夹')
        self.folder.returnPressed.connect(self.scan)
        pick = QPushButton('选择文件夹…'); pick.clicked.connect(self.pick_folder)
        again = QPushButton('重新扫描'); again.clicked.connect(self.scan)
        row.addWidget(QLabel('游戏库：')); row.addWidget(self.folder, 1); row.addWidget(pick); row.addWidget(again); v.addLayout(row)
        self.table = QTableWidget(0, len(self.COLS)); self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False); self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows); self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setMinimumHeight(180); self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.doubleClicked.connect(lambda _: self.push())   # a double click on a game pushes it
        h = self.table.horizontalHeader(); h.setSectionResizeMode(0, QHeaderView.Stretch); h.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4): h.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        v.addWidget(self.table, 1)
        drow = QHBoxLayout(); self.device = QComboBox(); self.device.setMinimumWidth(280)
        self.device.currentIndexChanged.connect(self.device_changed)
        r = QPushButton('刷新设备'); r.clicked.connect(self.refresh_devices)
        drow.addWidget(QLabel('设备：')); drow.addWidget(self.device, 1); drow.addWidget(r); v.addLayout(drow)
        b = QHBoxLayout(); self.push_btn = QPushButton('推送到设备'); self.push_btn.setMinimumHeight(34); self.push_btn.setEnabled(False)
        f2 = self.push_btn.font(); f2.setPointSize(f2.pointSize() + 1); f2.setBold(True); self.push_btn.setFont(f2)
        self.push_btn.clicked.connect(self.push)
        self.cancel_btn = QPushButton('取消'); self.cancel_btn.setEnabled(False); self.cancel_btn.clicked.connect(self.cancel)
        b.addWidget(self.push_btn, 1); b.addWidget(self.cancel_btn); v.addLayout(b)
        self.prog = QProgressBar(); self.prog.setRange(0, 1000); self.prog.setValue(0); v.addWidget(self.prog)
        self.status = QLabel(''); self.status.setWordWrap(True); v.addWidget(self.status)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(110); self.log.setVisible(False); v.addWidget(self.log)
        cfg = config.load()
        self.folder.setText(cfg.get('library') or cfg.get('out') or '')
        self.refresh_devices()
        self.scan(quiet=True)

    # ---- the library ----
    def adopt(self, result):
        """A port just finished here: point the library at its output folder, unless the player chose another one for the push tab"""
        if not result or not result.get('data'): return
        d = str(Path(result['data']).parent)
        if not self.folder.text().strip() or config.load().get('library') in ('', d):
            self.folder.setText(d); self.scan()

    def pick_folder(self):
        start = self.folder.text().strip() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, '存放已移植游戏的文件夹', start)
        if not d: return
        self.folder.setText(d); self.scan()

    def scan(self, quiet=False):
        root = self.folder.text().strip()
        if not root: 
            if not quiet: self.set_status('先选择存放移植结果的文件夹。')
            return
        if not Path(root).is_dir(): self.set_status(f'不是文件夹：{root}', red=True); return
        self.rows = library.scan(root)
        if self.rows: config.set_value('library', root)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            cells = (r['name'], r['package'] or '—', r['display'] or r['version'] or '—', size(r['bytes']) if r['bytes'] else '—', '')
            for j, c in enumerate(cells):
                it = QTableWidgetItem(c); self.table.setItem(i, j, it)
            if not r['apk']:
                self.table.item(i, 0).setForeground(QColor('#c62828'))
                self.table.item(i, 0).setToolTip('这个数据包旁没有同名 APK：请重新移植，或把 APK 放回数据包所在文件夹')
        if self.rows: self.table.selectRow(0)
        else: self.set_status(f'{root} 中没有已移植的游戏（需要 nx2a-data.json 与同名 APK）。', red=not quiet)
        if self.rows:
            total = sum(r['bytes'] for r in self.rows)
            self.set_status(f'找到 {len(self.rows)} 个游戏' + (f'，共 {size(total)}' if total else '') + f'（{root}）。')
        self.fill_installed(); self.update_buttons()

    def selected(self):
        i = self.table.currentRow()
        return self.rows[i] if 0 <= i < len(self.rows) else None

    def update_buttons(self):
        r = self.selected(); running = self.porter.running()
        ok = bool(r and r.get('apk') and self.device.currentData())
        self.push_btn.setEnabled(ok and not running)
        self.push_btn.setText(f'推送「{r["name"]}」到设备' if r and r.get('apk') else '推送到设备')
        self.table.setEnabled(not running); self.folder.setEnabled(not running)
        self.push_btn.setToolTip('' if ok or running else ('这个数据包旁没有 APK' if r and not r.get('apk') else '没有连接的设备'))

    # ---- the device ----
    def refresh_devices(self):
        cur = config.load().get('device')
        def got(rows, code):
            rows = rows if isinstance(rows, list) else []
            self.device.blockSignals(True); self.device.clear()
            for r in rows:
                if r.get('state') == 'device': self.device.addItem((f'{r["serial"]}  {r.get("info") or ""}').strip(), r['serial'])
            if not self.device.count():   # nothing usable: say why (a phone that has not been allowed yet, or none at all)
                bad = [r['serial'] for r in rows if r.get('state') == 'unauthorized']
                self.device.addItem('未授权：' + '、'.join(bad) + '（请在手机上允许 USB 调试）' if bad else '未连接设备', '')
            i = self.device.findData(cur)
            self.device.setCurrentIndex(i if i >= 0 else 0)
            self.device.blockSignals(False)
            self.refresh_installed(); self.update_buttons()
        call_json(self, ['devices', '--json'], got)

    def device_changed(self, _i=0):
        self.refresh_installed(); self.update_buttons()

    def refresh_installed(self):   # which of the listed games the chosen device already has (porter saves apps)
        serial = self.device.currentData()
        self.installed = set()
        if not serial: self.fill_installed(); return
        def got(rows, code):
            self.installed = set(rows) if isinstance(rows, list) else set()
            self.fill_installed()
        call_json(self, ['saves', 'apps', '--device', serial, '--json'], got)

    def fill_installed(self):
        for i, r in enumerate(self.rows):
            it = self.table.item(i, 4)
            if not it: continue
            if r['package'] and r['package'] in self.installed: it.setText('已安装'); it.setForeground(QColor('#2e7d32'))
            else: it.setText('未安装'); it.setForeground(QColor('#757575'))

    def showEvent(self, e):
        super().showEvent(e)
        if not self.porter.running(): QTimer.singleShot(0, self.refresh_devices)

    # ---- the push ----
    def set_status(self, msg, red=False):
        self.status.setText(msg); self.status.setStyleSheet('color:#c62828' if red else '')

    def push(self):
        r = self.selected()
        if not r: self.set_status('先在列表中选择一个游戏。', red=True); return
        if not r.get('apk'): self.set_status('这个数据包旁没有 APK：请重新移植，或把 APK 放回数据包所在文件夹。', red=True); return
        serial = self.device.currentData()
        if not serial: self.set_status('没有连接的设备：请用数据线连上手机，并在手机上允许 USB 调试。', red=True); return
        self.log.clear(); self.log.setVisible(True); self.prog.setRange(0, 0); self.prog.setValue(0); self.t0 = time.time()
        self.cancel_btn.setEnabled(True); self.push_btn.setEnabled(False); self.cancel_btn.setFocus()
        self.set_status(f'正在把「{r["name"]}」推送到 {serial}…')
        self.porter.start(['install', r['data'], '--apk', r['apk'], '--device', serial, '--progress', 'json'])

    def cancel(self):
        self.porter.cancel(); self.set_status('正在取消…')

    def on_event(self, ev):
        e = ev.get('ev')
        if e == 'stage':
            self.set_status(f'[{ev["k"]}/{ev["n"]}] {ev["name"]}' + (f' · {ev["step"]}' if ev.get('step') else '')); self.prog.setRange(0, 0)
        elif e == 'step':
            self.set_status(ev.get('name', '') + (f' · {ev["step"]}' if ev.get('step') else '')); self.prog.setRange(0, 0)
        elif e == 'progress':
            done, total = ev.get('done'), ev.get('total')
            count = '' if done is None else (f'{size(done)} / {size(total)}' if ev.get('unit') == 'B' and total else f'{done:,}' + (f' / {total:,}' if total else ''))
            self.set_status(ev['name'] + (f' · {ev["step"]}' if ev.get('step') else '') + (f' · {count}' if count else '')
                            + (f' · 预计剩余 {hms(ev["eta"])}' if ev.get('eta') else ''))
            if ev.get('frac') is not None: self.prog.setRange(0, 1000); self.prog.setValue(int(ev['frac'] * 1000))
        elif e == 'stage_end':
            if not ev.get('ok', True): self.set_status(f'{ev.get("name", "")} 失败', red=True)
        elif e == 'say':
            msg = ev.get('msg', ''); red = msg.startswith('[RED] ')
            self.log.appendPlainText(msg.removeprefix('[RED] '))
            if red: self.set_status(msg.removeprefix('[RED] '), red=True)

    def on_finished(self, code):
        self.cancel_btn.setEnabled(False)
        took = hms(time.time() - self.t0) if self.t0 else ''
        r = self.selected()
        if code == 0:
            self.prog.setRange(0, 1000); self.prog.setValue(1000)
            self.set_status(f'完成：「{r["name"] if r else "游戏"}」已在设备上' + (f'，用时 {took}' if took else '') + '。请在手机上打开它。')
            self.refresh_installed()
        else:
            self.prog.setRange(0, 1000); self.prog.setValue(0)
            self.set_status('已取消' if code == 130 else f'推送失败' + (f'（用时 {took}）' if took else '') + '：原因见下面的日志。', red=code != 130)
        self.update_buttons()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('nx2android'); self.resize(980, 820)
        self.tabs = QTabWidget()
        self.port = PortTab(self); self.push = PushTab(self); self.setup = SetupTab(self); self.games = GamesTab(); self.saves = SavesTab()
        self.tabs.addTab(self.port, '移植'); self.tabs.addTab(self.push, '推送'); self.tabs.addTab(self.setup, '设置'); self.tabs.addTab(self.games, '游戏'); self.tabs.addTab(self.saves, '存档')
        self.setCentralWidget(self.tabs)
        self.first = True

    def set_ready(self, ok):   # the first check: a missing requirement opens the Setup tab
        if self.first and not ok: self.tabs.setCurrentWidget(self.setup)
        self.first = False


def main(argv):
    app = QApplication(argv); app.setApplicationName('nx2android'); app.setStyle('Fusion')
    if os.name == 'nt': app.setFont(QFont('Microsoft YaHei UI', 9))   # 中文字形与行距，好过默认的 Segoe UI 回退
    app.setStyleSheet('QHeaderView::section { padding: 2px 6px; }')   # 表头留白，中文表头不再互相贴住
    app.setWindowIcon(QIcon(str(Path(__file__).parent / 'icon.png')))   # docs/images/icon.svg, rendered by devtools/make_app_icon.py
    w = Window(); w.show()
    return app.exec()
