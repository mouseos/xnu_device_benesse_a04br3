# SPDX-License-Identifier: MIT
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from payload import HEADER, SEGMENT, build


def kernel_fixture(chain=False, authenticated=False):
    """Synthetic Mach-O for host tests only; never flashed as an XNU kernel."""
    va = 0xfffffff007004000
    segment = struct.pack('<II16s4Q4I', 0x19, 72, b'__TEXT', va, 0x8000, 0, 0x4000, 7, 5, 0, 0)
    registers = [0] * 34
    registers[32] = va + 0x1000
    thread = struct.pack('<4I34Q', 5, 288, 6, 68, *registers)
    commands = segment + thread
    if chain:
        commands += struct.pack('<4I', 0x80000034, 16, 0x3000, 60)
    data = bytearray(0x4000)
    struct.pack_into('<8I', data, 0, 0xfeedfacf, 0x100000c, 0, 2, 3 if chain else 2, len(commands), 0, 0)
    data[32:32 + len(commands)] = commands
    if chain:
        struct.pack_into('<7I', data, 0x3000, 0, 28, 0, 0, 0, 0, 0)
        struct.pack_into('<2I', data, 0x301c, 1, 8)
        struct.pack_into('<IHHQIHH', data, 0x3024, 24, 16384, 6, 0, 0, 1, 0x1000)
        struct.pack_into('<Q', data, 0x1000, 0x1100 | ((1 << 63) if authenticated else 0))
    return bytes(data)


class PayloadTests(unittest.TestCase):
    def test_plain_kernel_and_header(self):
        data, info = build(kernel_fixture())
        header = HEADER.unpack_from(data)
        self.assertEqual(header[:3], (b'A04XNU01', 1, 1))
        self.assertEqual(header[4:6], (0x60000000, 0x18000000))
        physical, memory, offset, size = SEGMENT.unpack_from(data, HEADER.size)
        self.assertEqual((physical, memory, size), (0x61004000, 0x8000, 0x4000))
        self.assertEqual(data[offset:offset + size], kernel_fixture())
        self.assertEqual(info['chained_rebases'], 0)

    def test_rebase_of_source_kernel(self):
        data, info = build(kernel_fixture(chain=True))
        _, _, offset, _ = SEGMENT.unpack_from(data, HEADER.size)
        self.assertEqual(struct.unpack_from('<Q', data, offset + 0x1000)[0], 0xfffffff007005100)
        self.assertEqual(info['chained_rebases'], 1)

    def test_arm64e_authenticated_and_missing_entry_rejected(self):
        arm64e = bytearray(kernel_fixture()); struct.pack_into('<I', arm64e, 8, 2)
        bad_entry = bytearray(kernel_fixture()); struct.pack_into('<Q', bad_entry, 32 + 72 + 16 + 32 * 8, 0)
        for data in (bytes(arm64e), kernel_fixture(chain=True, authenticated=True), bytes(bad_entry)):
            with self.assertRaises(ValueError):
                build(data)

    def test_xnu_file_only_ctf_metadata(self):
        data = bytearray(kernel_fixture())
        commands = struct.unpack_from('<I', data, 20)[0]
        ctf = struct.pack('<II16s4Q4I', 0x19, 72, b'__CTF',
                          0xfffffff00700c000, 0, 0x4000, 16, 1, 1, 0, 0)
        data[32 + commands:32 + commands + 72] = ctf
        struct.pack_into('<2I', data, 16, 3, commands + 72)
        data += b'CTF_DEBUG_ONLY!!!'
        packed, info = build(bytes(data))
        self.assertEqual(info['segments'], 1)
        self.assertNotIn(b'CTF_DEBUG_ONLY!!!', packed)
        data[32 + commands + 8:32 + commands + 24] = b'__DATA'.ljust(16, b'\0')
        with self.assertRaises(ValueError):
            build(bytes(data))

    def test_bounds_and_memory_limit(self):
        for length in (0, 31, 100, 0x3fff):
            with self.assertRaises(ValueError):
                build(kernel_fixture()[:length])
        with self.assertRaises(ValueError):
            build(kernel_fixture(), mem_size=0x4000)
        bad = bytearray(kernel_fixture(chain=True))
        struct.pack_into('<H', bad, 0x3024 + 22, 0x8001)
        with self.assertRaises(ValueError):
            build(bytes(bad))


if __name__ == '__main__':
    unittest.main()
