# SPDX-License-Identifier: MIT
import json
import struct
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import hfsroot


class HFSRootTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('HFS_MKFS') and os.environ.get('HFS_FSCK'), 'set HFS_MKFS and HFS_FSCK for disk-format verification')
    def test_multiple_catalog_leaves_and_allocation_bitmap(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'data';source.write_bytes(bytes(range(256))*17)
            image=root/'root.img'
            with image.open('wb') as f:f.truncate(128*1024*1024)
            subprocess.run([os.environ['HFS_MKFS'],'-v','A04BR3Root','-b','4096',str(image)],check=True,capture_output=True)
            files={f'/System/Cache/item{i:03}':source for i in range(100)}
            files['/private/var/keybags/systembag.kb']=source
            result=hfsroot.populate(image,files,modes={'/private/var/keybags':0o700,'/private/var/keybags/systembag.kb':0o600},symlinks={'/var':'private/var','/tmp':'private/var/tmp'},directories=['/private/var/tmp','/private/var/mobile'],owners={'/private/var/mobile':(501,501)})
            self.assertEqual(result['files'],103)
            self.assertEqual(result['symlinks'],2)
            modes={};links={};owners={}
            with image.open('rb') as disk:
                disk.seek(1024);header=disk.read(512)
                block=struct.unpack_from('>I',header,40)[0]
                first=struct.unpack_from('>I',header,288)[0]
                length=struct.unpack_from('>Q',header,272)[0]
                disk.seek(first*block);catalog=disk.read(length)
            size=struct.unpack_from('>H',catalog,32)[0]
            for at in range(0,len(catalog),size):
                node=catalog[at:at+size]
                if node[8]!=255:continue
                count=struct.unpack_from('>H',node,10)[0]
                for i in range(count):
                    offset=struct.unpack_from('>H',node,size-2*(i+1))[0]
                    keylen=struct.unpack_from('>H',node,offset)[0]+2
                    chars=struct.unpack_from('>H',node,offset+6)[0]
                    name=node[offset+8:offset+8+chars*2].decode('utf-16be')
                    kind=struct.unpack_from('>H',node,offset+keylen)[0]
                    if kind==1 and name=='mobile':
                        owners[name]=struct.unpack_from('>II',node,offset+keylen+32)
                    if kind==2 and name in ('var','tmp'):
                        base=offset+keylen
                        mode=struct.unpack_from('>H',node,base+42)[0]
                        if mode&0o170000==0o120000:
                            self.assertEqual(node[base+48:base+56],b'slnkrhap')
                            length=struct.unpack_from('>Q',node,base+88)[0]
                            first=struct.unpack_from('>I',node,base+104)[0]
                            with image.open('rb') as disk:
                                disk.seek(first*block);links[name]=disk.read(length).decode()
                    if kind in (1,2) and name in ('keybags','systembag.kb'):
                        modes[name]=struct.unpack_from('>H',node,offset+keylen+42)[0]
            self.assertEqual(modes,{'keybags':0o40700,'systembag.kb':0o100600})
            self.assertEqual(links,{'var':'private/var','tmp':'private/var/tmp'})
            self.assertEqual(owners,{'mobile':(501,501)})
            check=subprocess.run([os.environ['HFS_FSCK'],'-fn',str(image)],text=True,capture_output=True)
            self.assertIn('appears to be OK',check.stdout+check.stderr)
