# SPDX-License-Identifier: BSD-2-Clause-Patent
from pathlib import Path
import struct
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import rogue_memory_stage as memory


def fixture():
    b = bytearray(8192)
    struct.pack_into('<16sHHIIIIIHHHHHH', b, 0,
                     b'\x7fELF\x01\x01\x01' + bytes(9), 2, 8, 1,
                     0xc0000001, 52, 0, 0, 52, 32, 1, 0, 0, 0)
    struct.pack_into('<8I', b, 52, 1, 128, 0xc0000000, 0, 4, 8, 5, 4)
    b[128:132] = b'TEST'
    struct.pack_into('<4IQI', b, 4096, 1, 32, 6, 24, 0x001600280036001e, 0)
    for i, base in enumerate((0xc0000000, 0x9fc02000, 0xbfc00000,
                              0xc0032000, 0xbfc01000, 0xcf600000)):
        struct.pack_into('<6I', b, 4128+i*24, 4+i, 1 if i < 3 else 2,
                         base, 4096, 4096, (i % 3)*4096)
    return b


class MemoryStage(unittest.TestCase):
    def test_table_permissions_boot_words_and_holes(self):
        blob = fixture()
        before = bytes(blob)
        manifest, buffers, table = memory.build(blob, 0x80000000, 0x81000000,
                                                0x82000000, 0, 0x32000)
        self.assertEqual(bytes(blob), before)
        self.assertEqual(buffers[1][:4], b'TEST')
        self.assertEqual(struct.unpack_from('<3I', table),
                         (0x02000013, 0x02000053, 0x02000093))
        self.assertEqual(struct.unpack_from('<I', table, 0x32*4)[0], 0x42040017)
        self.assertEqual(table[12:0x80], bytes(0x80-12))
        self.assertEqual(struct.unpack_from('<4Q', buffers[2], 4096),
                         (0x13000000, 0x82000000, 0x81002000, 0))
        self.assertFalse(manifest['firmware_execution_ready'])
        self.assertFalse(manifest['device_addresses_verified'])
        self.assertFalse(manifest['hardware_uploaded'])

    def test_invalid_ranges(self):
        valid = [0x80000000, 0x81000000, 0x82000000, 0, 0x32000]
        for index, value in ((0, 0), (0, -4096), (0, 0xfffff000),
                             (1, 0x80001000), (2, 0x82001000),
                             (2, 2**32), (3, 1), (3, -4096),
                             (4, 0), (4, 0x19000), (4, 32*1024*1024-4096)):
            args = valid.copy()
            args[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                memory.build(fixture(), *args)
