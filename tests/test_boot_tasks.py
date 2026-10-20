# SPDX-License-Identifier: MIT
import pathlib
import plistlib
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
import boot_tasks


def macho(config):
    payload=plistlib.dumps(config)
    header=struct.pack('<8I',0xfeedfacf,0x100000c,0,2,1,152,0,0)
    segment=bytearray(152);struct.pack_into('<II',segment,0,0x19,152)
    struct.pack_into('<I',segment,64,1);segment[72:88]=b'__config'.ljust(16,b'\0')
    struct.pack_into('<QI',segment,112,len(payload),184)
    return header+segment+payload


class BootTasks(unittest.TestCase):
    def test_required_task_and_internal_alias(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);(root/'sbin').mkdir();(root/'sbin/fsck').write_bytes(b'original')
            (root/'sbin/alias').symlink_to('fsck')
            binary=root/'launchd';binary.write_bytes(macho({'Boot':{'fsck':{'Program':'/sbin/fsck','RequireRun':True},'alias':{'Program':'/sbin/alias'},'optional':{'Program':'/missing'}}}))
            files,tasks=boot_tasks.donor_files(binary,root)
            self.assertEqual(files['/sbin/alias'].read_bytes(),b'original')
            self.assertFalse(tasks['optional']['present'])
            (root/'sbin/fsck').unlink()
            with self.assertRaises(ValueError):boot_tasks.donor_files(binary,root)

    def test_reject_escape_and_truncated_config(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);binary=root/'launchd'
            binary.write_bytes(macho({'Boot':{'bad':{'Program':'/../outside'}}}))
            with self.assertRaises(ValueError):boot_tasks.donor_files(binary,root)
            binary.write_bytes(macho({'Boot':{}})[:-30])
            with self.assertRaises(ValueError):boot_tasks.configuration(binary)
