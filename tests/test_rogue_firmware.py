# SPDX-License-Identifier: BSD-2-Clause-Patent
import importlib.util
import struct
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('rogue_firmware',Path(__file__).resolve().parents[1]/'tools/rogue_firmware.py')
fw=importlib.util.module_from_spec(spec);spec.loader.exec_module(fw)
def fixture():
 b=bytearray(8192);ident=b'\x7fELF\x01\x01\x01'+bytes(9)
 struct.pack_into('<16sHHIIIIIHHHHHH',b,0,ident,2,8,1,0x1001,52,0,0,52,32,1,0,0,0)
 struct.pack_into('<8I',b,52,1,128,0x1000,0x1000,4,8,5,4);b[128:132]=b'TEST'
 bvnc=sum(v<<s for v,s in zip(fw.BVNC,(48,32,16,0)))
 struct.pack_into('<4IQI',b,4096,1,32,6,24,bvnc,0)
 for n in range(6):struct.pack_into('<6I',b,4128+n*24,4+n,1 if n<3 else 2,0x1000+n*0x1000,16,16,(n%3)*16)
 return b
class Firmware(unittest.TestCase):
 def test_valid_segments_and_zero_initialization(self):
  manifest,buffers=fw.stage(fixture());self.assertEqual(buffers[1][:8],b'TEST\0\0\0\0');self.assertEqual(buffers[2],bytes(48));self.assertFalse(manifest['hardware_uploaded'])
 def test_incompatible_and_truncated_input(self):
  for position,value in ((4096,2),(4112,0),(4132,2),(52+16,4097),(52+20,17),(52+4,4094)):
   b=fixture();struct.pack_into('<I',b,position,value)
   with self.subTest(position=position),self.assertRaises(ValueError):fw.stage(b)
  with self.assertRaises(ValueError):fw.stage(bytes(4096))
 def test_overlap_and_segment_outside_layout(self):
  b=fixture();struct.pack_into('<I',b,4128+24+20,0)
  with self.assertRaisesRegex(ValueError,'overlapping allocation'):fw.stage(b)
  b=fixture();struct.pack_into('<I',b,52+8,0x90000000)
  with self.assertRaisesRegex(ValueError,'outside firmware'):fw.stage(b)
