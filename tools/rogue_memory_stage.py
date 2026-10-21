#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Offline MIPS memory candidate, never upload or execute it.

Addresses must come from a future device allocation handoff. CLI addresses alone
are assertions, not evidence of owned memory. Heap offsets are RGX firmware heap
allocation offsets, NOT MIPS ELF virtual addresses. Wrapper remaps, SLC/MMU setup,
firmware init structures and submission remain deliberately unimplemented.
"""
import argparse
import json
import struct
from pathlib import Path
from rogue_firmware import stage

PAGE = 4096
TABLE_BYTES = 32768
HEAP_BYTES = 32 * 1024 * 1024


def build(blob, code_pa, data_pa, table_pa, code_offset, data_offset):
    manifest, buffers = stage(blob)
    sections = {s['id']: s for s in manifest['sections']}
    # rgxinit.c reserves maximum code size (including dummy pages) before
    # data. Packing code/data consecutively would silently shift data PTEs.
    for sid, offset in ((4, code_offset), (7, data_offset)):
        section = sections[sid]
        if offset + section['offset'] != section['base'] - 0xc0000000:
            raise ValueError('heap allocation disagrees with MIPS linker address')
    ranges = [(code_pa, len(buffers[1]), PAGE),
              (data_pa, len(buffers[2]), PAGE),
              (table_pa, TABLE_BYTES, TABLE_BYTES)]
    for pa, size, alignment in ranges:
        if pa <= 0 or pa % alignment or size % PAGE or pa + size > 2**32:
            raise ValueError('unaligned or non-32bit physical allocation')
    for i, (pa, size, _) in enumerate(ranges):
        for other, count, _ in ranges[:i]:
            if pa < other + count and other < pa + size:
                raise ValueError('overlapping physical allocations')
    table = bytearray(TABLE_BYTES)
    for kind, pa, offset in ((1, code_pa, code_offset), (2, data_pa, data_offset)):
        size = len(buffers[kind])
        if offset < 0 or offset % PAGE or offset + size > HEAP_BYTES:
            raise ValueError('invalid firmware heap offset')
        for n in range(size // PAGE):
            index = (offset // PAGE + n) * 4
            if struct.unpack_from('<I', table, index)[0]:
                raise ValueError('overlapping firmware heap allocations')
            # Uncached, global, valid. Code RX; data RW and execute inhibited.
            flags = 0x13 if kind == 1 else 0x40000017
            struct.pack_into('<I', table, index, ((pa + n * PAGE) >> 6) | flags)
    boot, stack = sections[8], sections[9]
    if (boot['allocated'] != PAGE or stack['allocated'] != PAGE or
            boot['offset'] % PAGE or stack['offset'] % PAGE):
        raise ValueError('unsupported boot data/stack page layout')
    # rgxfwimageutils.c: four uint64 words at MIPS_BOOT_DATA + 0.
    # Stack argument is the page physical base, NOT top-of-stack.
    params = (0x13000000, table_pa, data_pa + stack['offset'], 0)
    struct.pack_into('<4Q', buffers[2], boot['offset'], *params)
    manifest.update(memory_stage_version=1, code_physical=code_pa,
                    data_physical=data_pa, table_physical=table_pa,
                    code_heap_offset=code_offset, data_heap_offset=data_offset,
                    boot_parameters=list(params), hardware_uploaded=False,
                    firmware_execution_ready=False, device_addresses_verified=False)
    return manifest, buffers, table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    for name in ('code-pa', 'data-pa', 'table-pa', 'code-offset', 'data-offset'):
        parser.add_argument('--' + name, type=lambda x: int(x, 0), required=True)
    args = parser.parse_args()
    manifest, buffers, table = build(args.firmware.read_bytes(), args.code_pa,
                                    args.data_pa, args.table_pa,
                                    args.code_offset, args.data_offset)
    args.out.mkdir(parents=True, exist_ok=False)
    for kind, name in ((1, 'code.bin'), (2, 'data.bin')):
        (args.out / name).write_bytes(buffers[kind])
    (args.out / 'mips-table.bin').write_bytes(table)
    (args.out / 'memory.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(args.out / 'memory.json')


if __name__ == '__main__':
    main()
