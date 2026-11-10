# The ported games on this computer, for the GUI's Push tab (`porter library`): a port leaves a data pack folder with nx2a-data.json
# (porter/datapack.py) and, beside it, the APK it was built into (<App>.apk; a folder of one .apk counts too). The scan reads only
# the manifests: a pack's RomFS holds tens of thousands of files, and none of them are opened here.
import os
from pathlib import Path

from porter import datapack

MAX_DEPTH = 4                            # how far under the chosen folder to look (a library of per-game folders is 2 deep)
OWN = ('RomFS', 'mods', 'aoc', 'sysdata')  # a pack's own folders: never descended into


def _apk_for(data):
    """The APK of the port whose pack is data: <App>.apk named after the pack, else the only .apk beside it"""
    stem = data.name[:-5] if data.name.lower().endswith('-data') else data.name
    exact = data.parent / f'{stem}.apk'
    if exact.is_file(): return exact
    near = [p for p in data.parent.glob('*.apk') if p.is_file()]
    return near[0] if len(near) == 1 else None


def entry(data):
    """One ported game, as the library and the GUI show it"""
    man = datapack.read_manifest(data) or {}
    apk = _apk_for(data)
    return {'data': str(data), 'apk': str(apk) if apk else '',
            'name': man.get('name') or data.name, 'package': man.get('package', ''),
            'version': man.get('version', ''), 'display': man.get('display', ''),
            'bytes': int(man.get('bytes') or 0), 'files': int(man.get('files') or 0),
            'made': man.get('made', ''), 'mtime': data.stat().st_mtime}


def scan(root, depth=MAX_DEPTH):
    """The ported games at or under root, newest first. A folder holding the manifest is a pack and is not descended into, so a library
    of several games costs one directory listing per game instead of a walk of every RomFS"""
    root = Path(root).expanduser()
    if root.is_file(): root = root.parent          # an APK or a file inside a game folder
    if not root.is_dir(): return []
    if (root / datapack.MANIFEST).is_file(): return [entry(root)]   # the pack itself was chosen
    out, seen = [], set()
    for cur, dirs, files in os.walk(root):
        here = Path(cur)
        if datapack.MANIFEST in files:             # a pack: take it, and go no deeper
            dirs[:] = []
            key = os.path.normcase(str(here))
            if key not in seen: seen.add(key); out.append(entry(here))
            continue
        if len(here.relative_to(root).parts) >= depth: dirs[:] = []
        else: dirs[:] = [d for d in dirs if d not in OWN and not d.startswith('.')]
    return sorted(out, key=lambda e: e['mtime'], reverse=True)
