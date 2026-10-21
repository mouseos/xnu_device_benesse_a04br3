# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Host execution of the actual platform-expert identity methods.

IOKit services are stubbed; this does not execute MobileGestalt or XNU.
"""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "kernel/iokit/Kernel/arm/A04BR3PlatformExpert.cpp"
STUB = r"""
#pragma once
#include <cstddef>
#include <cstring>
#define OSDeclareDefaultStructors(c)
#define OSDefineMetaClassAndStructors(c,b)
#define APPLE_KEXT_OVERRIDE
struct IORegistryEntry {
    IORegistryEntry *childFromPath(const char *, int) { return nullptr; }
    void release() {}
};
struct IOService : IORegistryEntry {};
struct IODTPlatformExpert : IOService {
    bool start(IOService *) { return true; }
    void registerService() {}
    void createNubs(void *, void *) {}
};
inline void *IODTFindMatchingEntries(IORegistryEntry *, int, const char *) { return nullptr; }
constexpr int gIODTPlane = 0, kIODTExclusive = 0;
inline void IOLog(const char *) {}
inline size_t strlcpy(char *dst, const char *src, size_t size) {
    size_t length = std::strlen(src);
    if (size) {
        size_t count = length < size - 1 ? length : size - 1;
        std::memcpy(dst, src, count);
        dst[count] = 0;
    }
    return length;
}
"""


class FormFactorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="a04br3-formfactor-")
        root = Path(cls.temp.name)
        for name in ("pexpert/arm64/board_config.h", "IOKit/IOPlatformExpert.h",
                     "IOKit/IODeviceTreeSupport.h", "IOKit/IOLib.h"):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "stub.h"\n')
        (root / "stub.h").write_text(STUB)
        (root / "harness.cpp").write_text(
            '#include "' + str(SOURCE) + '"\n'
            'extern "C" bool product(char *p, int n) { A04BR3PlatformExpert e; return e.getProductName(p,n); }\n'
            'extern "C" bool target(char *p, int n) { A04BR3PlatformExpert e; return e.getTargetName(p,n); }\n')
        subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-DA04BR3",
                        "-shared", "-fPIC", "-I", str(root), str(root / "harness.cpp"),
                        "-o", str(root / "identity.so")], check=True)
        cls.lib = ctypes.CDLL(str(root / "identity.so"))
        for fn in (cls.lib.product, cls.lib.target):
            fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
            fn.restype = ctypes.c_bool

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_tablet_product_and_real_board_target(self):
        product, target = ctypes.create_string_buffer(64), ctypes.create_string_buffer(64)
        self.assertTrue(self.lib.product(product, len(product)))
        self.assertTrue(self.lib.target(target, len(target)))
        self.assertEqual(product.value, b"iPad-A04BR3")
        self.assertEqual(target.value, b"a04br3")
        # Observed MG prefix predicate at 0x19c55f62c..0x19c55f640.
        # This checks the source's input to MG, not execution of Apple code.
        self.assertEqual(product.value[:4], b"iPad")

    def test_bounds_and_invalid_buffers(self):
        for fn in (self.lib.product, self.lib.target):
            for size in (0, -1, 8):
                self.assertFalse(fn(None, size))
            buffer = ctypes.create_string_buffer(b"sentinel")
            for size in (0, -1):
                self.assertFalse(fn(buffer, size))
                self.assertEqual(buffer.value, b"sentinel")
            self.assertTrue(fn(buffer, 1))
            self.assertEqual(buffer.raw[:2], b"\0e")
            self.assertTrue(fn(buffer, 4))
            self.assertEqual(buffer.raw[3], 0)
            self.assertEqual(buffer.raw[4:], b"inel\0")


if __name__ == "__main__":
    unittest.main()
