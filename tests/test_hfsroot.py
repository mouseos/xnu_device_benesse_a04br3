# SPDX-License-Identifier: MIT
import json
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
            result=hfsroot.populate(image,{f'/System/Cache/item{i:03}':source for i in range(100)})
            self.assertEqual(result['files'],100)
            check=subprocess.run([os.environ['HFS_FSCK'],'-fn',str(image)],text=True,capture_output=True)
            self.assertIn('appears to be OK',check.stdout+check.stderr)
