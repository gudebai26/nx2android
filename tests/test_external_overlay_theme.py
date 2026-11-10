"""The external controller consumes the native overlay's real atlas manifest."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def jdk_bin():
    """The JDK's bin (javac, java): the tool's own search, else where an installer puts one (a test run isolates the config, so a saved jdk
    path is not seen); None without a JDK"""
    from porter import toolchain
    jdk = toolchain.find_jdk()
    if jdk: return jdk / 'bin'
    for r in (os.environ.get('ProgramFiles'), os.environ.get('ProgramW6432'), 'C:/Program Files', 'D:/Program Files'):
        if not r: continue
        for d in sorted(Path(r).glob('jdk*')):
            if (d / 'bin' / ('javac.exe' if os.name == 'nt' else 'javac')).is_file(): return d / 'bin'
    return None


def exe(bin_, name):
    return str(bin_ / (name + ('.exe' if os.name == 'nt' else '')))


class ExternalOverlayThemeTest(unittest.TestCase):
    def test_both_layouts_and_invalid_manifest(self):
        bin_ = jdk_bin()
        if not bin_: self.skipTest('需要 JDK（javac / java）')
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            check = root / 'org/nx2a/Check.java'
            check.parent.mkdir(parents=True)
            check.write_text('''package org.nx2a;
import java.io.*;
public class Check {
 public static void main(String[] args) throws Exception {
  for (String layout : new String[]{"phone", "tablet"}) {
   OverlayTheme t = OverlayTheme.read(new FileReader(args[0]), layout);
   if (!t.image.equals(layout + ".png") || t.scale <= 0 || t.sprites.isEmpty()) throw new AssertionError(layout);
   boolean up=false, down=false, left=false, right=false;
   for (OverlayTheme.Sprite s : t.sprites) if (s.control.equals("dpad") && s.state.equals("idle")) {
    up |= s.direction.equals("up"); down |= s.direction.equals("down");
    left |= s.direction.equals("left"); right |= s.direction.equals("right");
   }
   if (!(up && down && left && right)) throw new AssertionError("missing dpad sprite");
  }
  try { OverlayTheme.read(new StringReader("layout\\tphone\\t1\\tbroken\\ta.png\\t2\\t1"), "phone"); throw new AssertionError(); }
  catch (IOException expected) {}
 }
}''')
            source = ROOT / 'nxnative/dm/android/java/org/nx2a/OverlayTheme.java'
            manifest = ROOT / 'nxnative/dm/android/themes/ergo/theme.tsv'
            subprocess.run([exe(bin_, 'javac'), '-d', t, str(source), str(check)], check=True)
            subprocess.run([exe(bin_, 'java'), '-cp', t, 'org.nx2a.Check', str(manifest)], check=True)


if __name__ == '__main__':
    unittest.main()
