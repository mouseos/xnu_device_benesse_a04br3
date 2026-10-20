# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import bootstrap_layout

class BootstrapLayout(unittest.TestCase):
    def test_native_aliases_and_timezone_links_are_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);base=root/'usr/share/zoneinfo.default';base.mkdir(parents=True)
            (base/'+VERSION').write_text('2026a\n');(base/'UTC').write_bytes(b'TZif')
            (base/'Alias').symlink_to('UTC')
            for alias,target in {'var':'private/var','tmp':'private/var/tmp','etc':'private/etc','usr/share/zoneinfo':'/var/db/timezone/zoneinfo'}.items():
                (root/alias).symlink_to(target)
            files,links,dirs=bootstrap_layout.timezone_layout(root)
            self.assertEqual(files['/usr/share/zoneinfo.default/UTC'].read_bytes(),b'TZif')
            self.assertEqual(links['/usr/share/zoneinfo.default/Alias'],'UTC')
            self.assertEqual(links['/var'],'private/var')
            self.assertIn('/private/var/db/timezone',dirs)
            (base/'+VERSION').unlink()
            with self.assertRaises(ValueError):bootstrap_layout.timezone_layout(root)
