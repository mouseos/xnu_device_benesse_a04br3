# SPDX-License-Identifier: MIT
import gzip
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from bootimg import build, build_from_source, empty_cpio, pad, parse, section, split_kernel


def template():
    dtb = struct.pack('>10I', 0xd00dfeed, 40, 40, 40, 40, 17, 16, 0, 0, 0)
    sections = []
    for name, payload in ((b'KERNEL', gzip.compress(b'reference', mtime=0) + dtb),
                          (b'ROOTFS', gzip.compress(empty_cpio(), mtime=0))):
        header = struct.pack('<II32s', 0x58881688, 0, name) + b'\xff' * 472
        sections.append(section(header, payload))
    header = bytearray(2048)
    header[:8] = b'ANDROID!'
    struct.pack_into('<10I', header, 8, len(sections[0]), 0x40080000,
                     len(sections[1]), 0x47000000, 0, 0x40f00000,
                     0x4e000000, 2048, 0, 0)
    return bytes(header) + b''.join(pad(s, 2048) for s in sections)


def shim():
    data = bytearray(256)
    data[56:60] = b'ARM\x64'
    struct.pack_into('<Q', data, 16, len(data))
    return bytes(data)


class BootImageTests(unittest.TestCase):
    def test_reproducible_roundtrip_and_alignment(self):
        original = template()
        image, manifest = build(original, shim())
        self.assertEqual(image, build(original, shim())[0])
        fields, (kernel, ramdisk), valid = parse(image)
        raw, dtb, consumed = split_kernel(kernel[512:])
        self.assertTrue(valid)
        self.assertEqual(raw, shim())
        self.assertEqual(kernel[512 + ((consumed + 511) // 512) * 512:], dtb)
        self.assertEqual(gzip.decompress(ramdisk[512:]), empty_cpio())
        self.assertEqual(fields[1], 0x40080000)
        self.assertFalse(manifest['hardware_tested'])

    def test_build_from_tracked_configuration(self):
        board = json.loads((Path(__file__).resolve().parents[1] / 'board.json').read_text())
        _, parts, _ = parse(template())
        _, dtb, _ = split_kernel(parts[0][512:])
        image, manifest = build_from_source(dtb, shim(), board['boot_image'])
        fields, parts, valid = parse(image)
        self.assertTrue(valid)
        self.assertEqual(fields[3], 0x47000000)
        self.assertEqual(parts[0][40:512], b'\xff' * 472)
        self.assertEqual(parts[1][40:512], b'\xff' * 472)
        self.assertEqual(manifest['input_kind'], 'tracked DTS and board configuration')
        self.assertNotIn('template_sha256', manifest)

    def test_bad_template_layout_rejected(self):
        for offset, value in ((8, 0xffffffff), (12, 0x80000),
                              (24, 1), (36, 4096), (40, 1), (2052, 1)):
            with self.subTest(offset=offset):
                bad = bytearray(template())
                struct.pack_into('<I', bad, offset, value)
                with self.assertRaises(ValueError):
                    build(bytes(bad), shim())

    def test_truncation_and_invalid_shim(self):
        for bad in (b'', template()[:2048], template()[:2600]):
            with self.assertRaises(ValueError):
                build(bad, shim())
        with self.assertRaises(ValueError):
            build(template(), b'not-an-image')

    def test_corrupt_or_ambiguous_dtb_rejected(self):
        for tail in (b'', b'\xd0\x0d\xfe\xed', bytes(40),
                     struct.pack('>10I', 0xd00dfeed, 40, 0, 0, 0, 0, 0, 0, 0, 0) * 2):
            with self.assertRaises(ValueError):
                split_kernel(gzip.compress(b'diagnostic', mtime=0) + tail)


if __name__ == '__main__':
    unittest.main()
