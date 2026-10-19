# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from gpt import build, verify

ROOT = Path(__file__).resolve().parents[1]


class GptTests(unittest.TestCase):
    def layout(self, name='xnu'):
        return json.loads((ROOT / 'layouts' / (name + '.json')).read_text())

    def test_both_headers_and_protective_mbr(self):
        layout = self.layout(); primary, secondary, offset = build(layout)
        self.assertEqual(len(primary), 34 * 512)
        self.assertEqual(len(secondary), 33 * 512)
        self.assertEqual(primary[510:512], b'\x55\xaa')
        self.assertEqual(offset + len(secondary), layout['disk_sectors'] * 512)
        self.assertEqual(primary[1024:], secondary[:-512])
        verify(primary[512:1024], primary[1024:], 1, layout['disk_sectors'] - 1, 2)
        verify(secondary[-512:], secondary[:-512], layout['disk_sectors'] - 1, 1, offset // 512)

    def test_bootchain_and_calibration_do_not_move(self):
        old = {p['name']: p for p in self.layout('android-gsi')['partitions']}
        new = {p['name']: p for p in self.layout()['partitions']}
        for name in ('proinfo', 'nvram', 'protect1', 'protect2', 'persist', 'seccfg',
                     'lk', 'lk2', 'recovery', 'para', 'logo', 'expdb', 'tee1', 'tee2',
                     'kb', 'dkb', 'flashinfo'):
            self.assertEqual(new[name], old[name], name)
        self.assertEqual(new['boot']['sectors'] * 512, 128 * 1024 * 1024)
        self.assertEqual(new['boot']['first_lba'], 294912)
        self.assertEqual(new['system']['first_lba'], new['boot']['first_lba'] + new['boot']['sectors'])
        self.assertEqual(new['system']['first_lba'] + new['system']['sectors'], new['flashinfo']['first_lba'])
        self.assertFalse({'metadata', 'cache', 'userdata', 'vendor'} & new.keys())

    def test_overlaps_duplicates_and_out_of_bounds_are_rejected(self):
        layout = self.layout()
        for key, value in (('first_lba', 0), ('first_lba', 1024),
                           ('sectors', 0), ('sectors', layout['disk_sectors'])):
            bad = copy.deepcopy(layout)
            bad['partitions'][8][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                build(bad)
        bad = copy.deepcopy(layout); bad['partitions'].append(bad['partitions'][0])
        with self.assertRaises(ValueError):
            build(bad)

    def test_corrupt_header_and_table_fail_crc(self):
        primary, secondary, offset = build(self.layout())
        header = bytearray(primary[512:1024]); header[40] ^= 1
        with self.assertRaises(ValueError):
            verify(bytes(header), primary[1024:], 1, self.layout()['disk_sectors'] - 1, 2)
        entries = bytearray(primary[1024:]); entries[0] ^= 1
        with self.assertRaises(ValueError):
            verify(primary[512:1024], bytes(entries), 1, self.layout()['disk_sectors'] - 1, 2)


if __name__ == '__main__':
    unittest.main()
