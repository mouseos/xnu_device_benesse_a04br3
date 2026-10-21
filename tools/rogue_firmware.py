#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Validate and stage an unmodified MT8167 Rogue MIPS firmware image.

Format reference: MediaTek m1.11ED5425693 rgx_fw_info.h and
rgxfwimageutils.c, board source eb47f42e1f84fe5716ef239620725f82718a620a.
No disassembly, firmware modification, hardware upload or OS installation.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

BVNC = (22, 40, 54, 30)
LIMIT = 32 * 1024 * 1024

def stage(blob):
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)
    require(4096 < len(blob) <= LIMIT, 'firmware size out of range')
    info = len(blob) - 4096
    version, header, count, stride, raw_bvnc, flags = struct.unpack_from('<4IQI', blob, info)
    require((version, header, stride) == (1, 32, 24), 'unsupported firmware info format')
    require(0 < count <= 10 and header + count * stride <= 4096, 'invalid layout table size')
    bvnc = tuple((raw_bvnc >> shift) & 65535 for shift in (48, 32, 16, 0))
    require(bvnc == BVNC, 'firmware BVNC does not match MT8167 core')
    require(flags == 0, 'unsupported firmware compatibility flags')
    entries = []
    sizes = {1: 0, 2: 0}
    for i in range(count):
        sid, kind, base, maximum, allocated, offset = struct.unpack_from('<6I', blob, info + header + i * stride)
        require(sid in range(4, 10) and kind == (1 if sid < 7 else 2), 'unsupported firmware section')
        require(not any(e['id'] == sid for e in entries), 'duplicate section id')
        require(0 < allocated <= maximum and base + allocated <= 2**32 and offset + allocated <= LIMIT, 'invalid section bounds')
        for previous in entries:
            require(base + allocated <= previous['base'] or previous['base'] + previous['allocated'] <= base, 'overlapping firmware addresses')
            if previous['kind'] == kind:
                require(offset + allocated <= previous['offset'] or previous['offset'] + previous['allocated'] <= offset, 'overlapping allocation offsets')
        entries.append(dict(id=sid, kind=kind, base=base, maximum=maximum, allocated=allocated, offset=offset))
        sizes[kind] = max(sizes[kind], offset + allocated)
    require({e['id'] for e in entries} == set(range(4, 10)), 'missing MIPS code/data/boot/stack section')
    require(blob[:7] == b'\x7fELF\x01\x01\x01', 'expected ELF32 little-endian firmware')
    elf = struct.unpack_from('<16sHHIIIIIHHHHHH', blob)
    require(elf[1:4] == (2, 8, 1) and elf[8] == 52 and elf[9] == 32, 'expected MIPS executable ELF')
    phoff, phnum = elf[5], elf[10]
    require(0 < phnum <= 64 and phoff >= 52 and phoff + 32 * phnum <= info, 'invalid ELF program table')
    buffers = {kind: bytearray(size) for kind, size in sizes.items()}
    segments = []
    for i in range(phnum):
        typ, offset, address, _, filesz, memsz, permissions, alignment = struct.unpack_from('<8I', blob, phoff + i * 32)
        if typ != 1:
            continue
        require(filesz <= memsz and offset + filesz <= info and address + memsz <= 2**32, 'invalid ELF segment bounds')
        matches = [e for e in entries if e['base'] <= address and address + memsz <= e['base'] + e['allocated']]
        require(len(matches) == 1, 'segment outside firmware allocation')
        entry = matches[0]
        target = entry['offset'] + address - entry['base']
        for old in segments:
            require(old['kind'] != entry['kind'] or target + memsz <= old['target'] or old['target'] + old['memsz'] <= target, 'overlapping ELF segments')
        buffers[entry['kind']][target:target + filesz] = blob[offset:offset + filesz]
        segments.append(dict(kind=entry['kind'], target=target, address=address, filesz=filesz, memsz=memsz, permissions=permissions, alignment=alignment))
    require(segments and any(s['permissions'] & 1 and s['address'] <= (elf[4] & ~1) < s['address'] + s['memsz'] for s in segments), 'entry point outside executable segment')
    manifest = dict(bvnc='.'.join(map(str, bvnc)), source_sha256=hashlib.sha256(blob).hexdigest(), entry_point=elf[4], sections=entries, segments=segments, allocation_bytes={str(k): v for k, v in sizes.items()}, hardware_uploaded=False, gpu_rendering_verified=False)
    return manifest, buffers

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', required=True, type=Path, help='firmware supplied from your matching board vendor image')
    parser.add_argument('--out', required=True, type=Path, help='new output directory; never installed automatically')
    args = parser.parse_args()
    manifest, buffers = stage(args.firmware.read_bytes())
    args.out.mkdir(parents=True, exist_ok=False)
    for kind, name in ((1, 'code.bin'), (2, 'data.bin')):
        (args.out / name).write_bytes(buffers[kind])
    (args.out / 'firmware.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(args.out / 'firmware.json')

if __name__ == '__main__':
    main()
