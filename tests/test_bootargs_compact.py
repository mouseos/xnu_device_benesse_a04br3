# SPDX-License-Identifier: MIT
"""Actual pinned XNU parser checks for the packed board numeric arguments."""
import ctypes as C
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bootargs_compact import compact_for_handoff, NUMERIC_ONE_KEYS


class BootArguments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='a04-bootargs-')
        cls.source = ROOT / 'tests/fixtures/apple_bootargs/parser.c'
        library = Path(cls.tmp.name) / 'parser.so'
        subprocess.run(['clang', '-std=c11', '-O2', '-shared', '-fPIC',
                        '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                        str(cls.source), '-o', str(library)], check=True)
        cls.lib = C.CDLL(str(library))
        cls.lib.a04_test_parse.argtypes = [C.c_char_p, C.c_char_p, C.c_void_p, C.c_int]
        cls.lib.a04_test_parse.restype = C.c_int

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_short_command_is_identical(self):
        command = '-v a04br3.console=1 a04br3.fp=2 a04br3.display_probe=1'
        self.assertEqual(compact_for_handoff(command), command)

    def test_actual_parser_values_and_unchanged_raw_consumers(self):
        ids = ' '.join(f'a04br3.{key}=' + char * 40 for key, char in
                       [('kernel', 'a'), ('device', 'b'), ('bootshim', 'c')])
        known = ' '.join(key + '=1' for key in sorted(NUMERIC_ONE_KEYS))
        base = ids + ' ' + known + ' a04br3.display_probe=1 unknown=1 cpus=4'
        command = base + ' filler=' + 'x' * (620 - len(base) - 8)
        self.assertEqual(len(command), 620)
        compact = compact_for_handoff(command)
        self.assertEqual(len(compact), 604)
        self.assertIn(ids, compact)
        self.assertIn('a04br3.display_probe=1 unknown=1 cpus=4', compact)
        for key in sorted(NUMERIC_ONE_KEYS):
            for value_type in (C.c_uint8, C.c_uint16, C.c_uint32, C.c_uint64):
                for text in (command, compact):
                    value = value_type(0)
                    found = self.lib.a04_test_parse(text.encode(), key.encode(),
                                                   C.byref(value), C.sizeof(value))
                    self.assertEqual((found, value.value), (1, 1), key)

    def test_duplicates_order_and_non_one_modes_are_preserved(self):
        prefix = ('a04br3.fp=2 a04br3.no_va=0 a04br3.consoleExtra=1 '
                  'a04br3.console=1 a04br3.console=0 ')
        command = prefix + ' '.join(key + '=1' for key in sorted(NUMERIC_ONE_KEYS))
        command += ' a04br3.fp=2 a04br3.no_va=0 a04br3.console=0'
        command += ' ' + 'q' * (610 - len(command) - 1)
        compact = compact_for_handoff(command)
        self.assertTrue(compact.startswith(prefix.replace('console=1 ', 'console ')))
        for key, expected in [('a04br3.fp', 2), ('a04br3.no_va', 0),
                              ('a04br3.console', 0), ('a04br3.consoleExtra', 1)]:
            for text in (command, compact):
                value = C.c_uint32(999)
                self.assertEqual(self.lib.a04_test_parse(text.encode(), key.encode(),
                                                        C.byref(value), 4), 1)
                self.assertEqual(value.value, expected)

    def test_unfit_and_invalid_input_never_get_truncated(self):
        for command in ('x' * 608, 'unknown=1 ' + 'x' * 610, 'a\0b', '日本語'):
            with self.assertRaises((ValueError, UnicodeEncodeError)):
                compact_for_handoff(command)

    def test_actual_parser_under_sanitizers(self):
        harness = Path(self.tmp.name) / 'sanitizer.c'
        harness.write_text('#include <assert.h>\n#include <stdint.h>\n' +
                           '#include "' + str(self.source) + '"\nint main(void){\n' +
                           '\n'.join(' {uint64_t value=0; char arg[]="' + key +
                           ' next=0"; assert(a04_test_parse(arg,"' + key +
                           '",&value,8)==1 && value==1);}'
                           for key in sorted(NUMERIC_ONE_KEYS)) + '\nreturn 0;}\n')
        executable = harness.with_suffix('')
        subprocess.run(['clang', '-std=c11', '-O1', '-g',
                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                        str(harness), '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    unittest.main()
