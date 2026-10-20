# SPDX-License-Identifier: MIT
import errno
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

class RangeView(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('RUN_FUSE_TEST')=='1','set RUN_FUSE_TEST=1 for actual read-only FUSE tests')
    def test_source_offsets_lengths_eof_and_readonly_mount(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);program=root/'view';mount=root/'mount';mount.mkdir()
            source=Path(__file__).resolve().parents[1]/'tools/range_view.c'
            flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','fuse'],text=True))
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(source),*flags,'-o',str(program)],check=True)
            data=bytes(range(256))*32;image=root/'image';image.write_bytes(data)
            high=bytes(range(256))*4
            with image.open('r+b') as stream:
                stream.seek((1<<32)+129);stream.write(high);stream.truncate((1<<32)+4096)
            manifest=root/'ranges.tsv';manifest.write_text('89 385 first.bin\n1001 bff second.bin\n100000081 400 high.bin\n')
            subprocess.run([str(program),str(image),str(manifest),str(mount)],check=True)
            try:
                self.assertEqual((mount/'first.bin').read_bytes(),data[137:1038])
                self.assertEqual((mount/'second.bin').read_bytes(),data[4097:7168])
                self.assertEqual((mount/'high.bin').read_bytes(),high)
                self.assertEqual(sorted(x.name for x in mount.iterdir()),['first.bin','high.bin','second.bin'])
                with (mount/'second.bin').open('rb') as stream:
                    stream.seek(3070);self.assertEqual(stream.read(16),data[7167:7168]);self.assertEqual(stream.read(1),b'')
                with self.assertRaises(OSError) as failure:os.open(mount/'first.bin',os.O_WRONLY)
                self.assertIn(failure.exception.errno,(errno.EROFS,errno.EACCES))
            finally:subprocess.run(['fusermount','-u',str(mount)],check=True)
            manifest.write_text('1 ffffffff bad.bin\n')
            self.assertNotEqual(subprocess.run([str(program),str(image),str(manifest),str(mount)]).returncode,0)
