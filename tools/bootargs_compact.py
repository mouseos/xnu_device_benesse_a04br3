# SPDX-License-Identifier: MIT
"""Fit known numeric board switches without changing the boot_args ABI.

XNU PE_parse_boot_argn/getval maps a bare numeric key to integer1. These
specific consumers all use that API. Other flags must retain their spelling:
in particular bootshim matches the literal a04br3.display_probe=1.
"""
import re

BOOT_LINE_BYTES = 608
NUMERIC_ONE_KEYS = frozenset((
    'a04br3.console', 'a04br3.sample_uart', 'a04br3.prepare_root',
    'a04br3.msdc_dma', 'a04br3.msdc_probe', 'a04br3fb_diff',
    'a04br3.no_va', 'a04br3.fp',
))
# PE recognizes space/tab separators. A following separate '=' changes its
# bare-key parsing, so retain the explicit value in that case as well.
ONE_TOKEN = re.compile(r'(?<![^ \t])(' + '|'.join(
    re.escape(key) for key in sorted(NUMERIC_ONE_KEYS)) +
    r')=1(?=[ \t]|$)(?![ \t]+=)')


def compact_for_handoff(command):
    encoded = command.encode('ascii')
    if b'\0' in encoded:
        raise ValueError('invalid kernel command line: embedded NUL')
    if len(encoded) < BOOT_LINE_BYTES:
        return command
    compact = ONE_TOKEN.sub(r'\1', command)
    if len(compact.encode('ascii')) >= BOOT_LINE_BYTES:
        raise ValueError('kernel command line exceeds 607 bytes after safe '
                         f'numeric compaction ({len(compact)} bytes)')
    return compact
