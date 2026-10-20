# SPDX-License-Identifier: MIT
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from fullroot_flash import allocated_runs,copy_range

class FullFilesystemPlan(unittest.TestCase):
    def test_complete_allocated_data_does_not_depend_on_old_contents(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);block=4096;count=128
            data=bytearray(b'X'*(block*count));header=bytearray(512);header[:2]=b'HX'
            struct.pack_into('>II',header,40,block,count)
            struct.pack_into('>QII',header,112,16,0,1);struct.pack_into('>II',header,128,2,1)
            data[1024:1536]=header;bitmap=bytearray(16)
            allocated={0,2,50,51,127}
            for i in allocated:bitmap[i//8]|=0x80>>(i%8)
            data[2*block:2*block+16]=bitmap
            image=root/'root.img';image.write_bytes(data);target=bytearray(b'Z'*len(data))
            with image.open('rb') as source:
                for i,(offset,length) in enumerate(allocated_runs(image)):
                    part=root/f'part{i}';copy_range(source,part,offset,length)
                    target[offset:offset+length]=part.read_bytes()
            for i in allocated:self.assertEqual(target[i*block:(i+1)*block],data[i*block:(i+1)*block])
            self.assertEqual(target[90*block:(90+1)*block],b'Z'*block)
            struct.pack_into('>I',data,1024+44,count+1);image.write_bytes(data)
            with self.assertRaises(ValueError):allocated_runs(image)
