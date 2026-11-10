# Unit tests of the port library scan behind the GUI's Push tab and `porter library`: which folders are ported games, and which APK belongs to
# each (python -m unittest discover tests). They need no game files or device.
import json, os, sys, tempfile, time, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault('NX2A_HOME', tempfile.mkdtemp(prefix='nx2a_test_home_'))

from porter import library   # noqa: E402


def pack(root, folder, name, package, apk=None, apk_names=(), rows=1):
    """A port as the pipeline leaves it: <folder>/<apk or name>.apk beside <its stem>-data/ with the manifest"""
    d = Path(root) / folder; d.mkdir(parents=True, exist_ok=True)
    stem = apk or name
    data = d / f'{stem}-data'; (data / 'RomFS').mkdir(parents=True, exist_ok=True)
    (data / 'RomFS' / 'a.bin').write_bytes(b'x' * rows)
    (data / 'nx2a-data.json').write_text(json.dumps({'name': name, 'package': package, 'version': 'v65536', 'display': '1.0.0',
                                                     'bytes': 15357002979, 'files': 41084, 'made': '2026-11-10 14:35:48'}), encoding='utf-8')
    for a in apk_names or (f'{stem}.apk',): (d / a).write_bytes(b'PK\x03\x04')
    return d, data


class Library(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='nx2a_test_lib_'))

    def tearDown(self):
        import shutil; shutil.rmtree(self.root, ignore_errors=True)

    def test_empty(self):
        self.assertEqual(library.scan(self.root), [])
        self.assertEqual(library.scan(self.root / 'nope'), [])

    def test_pack_and_apk(self):
        d, data = pack(self.root, 'Game', 'Alpha', 'org.nx2a.alpha')
        rows = library.scan(self.root)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r['name'], r['package'], r['display'], r['version']), ('Alpha', 'org.nx2a.alpha', '1.0.0', 'v65536'))
        self.assertEqual(Path(r['data']), data)
        self.assertEqual(Path(r['apk']), d / 'Alpha.apk')
        self.assertEqual((r['bytes'], r['files']), (15357002979, 41084))

    def test_apk_with_another_name_and_its_own_folder(self):
        d, data = pack(self.root, '塞尔达传说旷野之息', 'The Legend of Zelda', 'org.nx2a.botw', apk='ZeldaBreathOfTheWild')
        r = library.scan(self.root)[0]                    # the APK is not named after the game, but it is the only one there
        self.assertEqual(Path(r['apk']), d / 'ZeldaBreathOfTheWild.apk')
        self.assertEqual(r['name'], 'The Legend of Zelda')

    def test_several_apks_beside_a_pack_are_not_guessed(self):
        pack(self.root, 'Game', 'Alpha', 'org.nx2a.alpha', apk_names=('One.apk', 'Two.apk'))
        r = library.scan(self.root)[0]
        self.assertEqual(r['apk'], '')                    # how many APKs a pack has is the port's record, not a guess from the folder
        self.assertEqual(r['package'], 'org.nx2a.alpha')

    def test_nested_and_newest_first(self):
        a, _ = pack(self.root, 'A', 'Alpha', 'org.nx2a.alpha')
        pack(self.root, 'x/B', 'Beta', 'org.nx2a.beta')
        old = time.time() - 600; os.utime(a, (old, old))
        self.assertEqual([r['name'] for r in library.scan(self.root)], ['Beta', 'Alpha'])
        pack(self.root, 'x/Gamma', 'Gamma', 'org.nx2a.gamma')
        self.assertEqual({r['name'] for r in library.scan(self.root)}, {'Alpha', 'Beta', 'Gamma'})

    def test_depth_limit(self):
        pack(self.root, 'a/b/c/d/e', 'Deep', 'org.nx2a.deep')   # five folders down: past MAX_DEPTH, and none of it is walked
        self.assertEqual(library.scan(self.root), [])

    def test_pointed_at_the_pack_or_a_file(self):
        d, data = pack(self.root, 'Game', 'Alpha', 'org.nx2a.alpha')
        self.assertEqual([r['name'] for r in library.scan(data)], ['Alpha'])
        self.assertEqual([r['name'] for r in library.scan(d / 'Alpha.apk')], ['Alpha'])

    def test_romfs_is_never_walked(self):
        d, data = pack(self.root, 'Game', 'Alpha', 'org.nx2a.alpha')
        (data / 'RomFS' / 'nx2a-data.json').write_text('{}', encoding='utf-8')   # a stray manifest inside a pack is not a second game
        self.assertEqual(len(library.scan(self.root)), 1)


if __name__ == '__main__':
    unittest.main()
