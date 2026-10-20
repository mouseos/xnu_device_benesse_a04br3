# SPDX-License-Identifier: MIT
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from hfsdelta import plan

class DeltaTests(unittest.TestCase):
    def test_ranges_reconstruct_new_image_within_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);a=p/'a';b=p/'b'
            old=bytearray(8192);old[1024:1026]=b'H+'
            new=bytearray(old);new[1536:2048]=b'A'*512;new[2560:3584]=b'B'*1024
            a.write_bytes(old);b.write_bytes(new)
            result=plan(a,b,p/'delta',4096,8192,512)
            self.assertEqual(result['write_bytes'],1536)
            self.assertEqual(len(result['ranges']),2)
            rebuilt=bytearray(old)
            for r in result['ranges']:
                self.assertGreaterEqual(r['absolute_offset'],4096)
                self.assertLessEqual(r['absolute_offset']+r['length'],12288)
                rebuilt[r['offset']:r['offset']+r['length']]=Path(r['file']).read_bytes()
            self.assertEqual(rebuilt,new)
    def test_refuses_mismatched_size_and_small_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);a=p/'a';b=p/'b';data=bytearray(4096);data[1024:1026]=b'H+'
            a.write_bytes(data);b.write_bytes(data)
            with self.assertRaises(ValueError):plan(a,b,p/'bad',0,512)
            b.write_bytes(data+bytes(512))
            with self.assertRaises(ValueError):plan(a,b,p/'bad',0,8192)
