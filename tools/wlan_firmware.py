#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Validate explicit MT6630 MTKW input without changing encrypted section bytes."""
import hashlib
from pathlib import Path
import struct
import zlib

LIMIT = 16 * 1024 * 1024


def stage(blob):
    if not 32 <= len(blob) <= LIMIT:
        raise ValueError('MTKW length outside production bounds')
    magic, crc, count, reserved = struct.unpack_from('<4I', blob)
    if magic != 0x574b544d or reserved or not 1 <= count <= 16 or len(blob) < 16+16*count:
        raise ValueError('invalid MTKW header')
    if zlib.crc32(blob[8:]) != crc:
        raise ValueError('MTKW CRC mismatch')
    regions = []
    for i in range(count):
        offset, reserved, size, address = struct.unpack_from('<4I', blob, 16+16*i)
        if (reserved or offset < 16+16*count or offset > len(blob) or not size or
                size > len(blob)-offset or size-1 > 0xffffffff-address):
            raise ValueError('invalid MTKW region')
        for r in regions:
            if (offset < r['offset']+r['bytes'] and r['offset'] < offset+size or
                    address < r['address']+r['bytes'] and r['address'] < address+size):
                raise ValueError('overlapping MTKW regions')
        regions.append(dict(offset=offset, bytes=size, address=address))
    return dict(format='MTKW', bytes=len(blob), source_sha256=hashlib.sha256(blob).hexdigest(),
                crc32=f'{crc:08x}', regions=regions, firmware_running=False)


def load(path):
    with Path(path).open('rb') as stream:
        blob = stream.read(LIMIT+1)
    return blob, stage(blob)
