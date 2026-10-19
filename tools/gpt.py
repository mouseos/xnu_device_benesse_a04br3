#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create both GPT copies from tracked layout definitions; no device access."""
import argparse
import json
from pathlib import Path
import struct
import uuid
import zlib


def build(layout):
    sector, sectors, count = layout['sector_bytes'], layout['disk_sectors'], layout['entry_count']
    if sector != 512 or count != 128 or sectors < 2048:
        raise ValueError('unsupported GPT geometry')
    array_sectors = count * 128 // sector
    first, last = 2 + array_sectors, sectors - array_sectors - 2
    entries = bytearray(count * 128)
    occupied, names = [], set()
    if len(layout['partitions']) > count:
        raise ValueError('too many partitions')
    for index, p in enumerate(layout['partitions']):
        name = p['name']; start = p['first_lba']; size = p['sectors']; end = start + size - 1
        if name in names or not name or len(name.encode('utf-16le')) > 72:
            raise ValueError('duplicate or invalid partition name')
        if size <= 0 or start < first or end > last:
            raise ValueError('partition outside usable disk')
        if any(start <= b and a <= end for a, b in occupied):
            raise ValueError('overlapping partitions')
        occupied.append((start, end)); names.add(name)
        at = index * 128
        entries[at:at + 16] = uuid.UUID(p['type_guid']).bytes_le
        entries[at + 16:at + 32] = uuid.UUID(p['unique_guid']).bytes_le
        struct.pack_into('<QQQ', entries, at + 32, start, end, p.get('attributes', 0))
        entries[at + 56:at + 128] = name.encode('utf-16le').ljust(72, b'\0')
    def header(current, backup, table):
        h = bytearray(sector)
        struct.pack_into('<8sIIIIQQQQ16sQIII', h, 0, b'EFI PART', 0x10000, 92, 0, 0,
                         current, backup, first, last, uuid.UUID(layout['disk_guid']).bytes_le,
                         table, count, 128, zlib.crc32(entries))
        struct.pack_into('<I', h, 16, zlib.crc32(h[:92]))
        return bytes(h)
    mbr = bytearray(sector)
    mbr[446:462] = struct.pack('<B3sB3sII', 0, b'\0\x02\0', 0xee,
                               b'\xff\xff\xff', 1, min(sectors - 1, 0xffffffff))
    mbr[510:512] = b'\x55\xaa'
    primary = bytes(mbr) + header(1, sectors - 1, 2) + entries
    backup_lba = sectors - 1 - array_sectors
    secondary = bytes(entries) + header(sectors - 1, 1, backup_lba)
    verify(primary[512:1024], entries, 1, sectors - 1, 2)
    verify(secondary[-512:], entries, sectors - 1, 1, backup_lba)
    return bytes(primary), secondary, backup_lba * sector


def verify(header, entries, current, backup, table):
    fields = struct.unpack_from('<8sIIIIQQQQ16sQIII', header)
    copy = bytearray(header[:fields[2]])
    struct.pack_into('<I', copy, 16, 0)
    if (fields[0] != b'EFI PART' or fields[5:7] != (current, backup) or
            fields[10] != table or zlib.crc32(copy) != fields[3] or
            zlib.crc32(entries) != fields[13]):
        raise ValueError('GPT integrity verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', type=Path, default=Path(__file__).resolve().parents[1] / 'layouts/xnu.json')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text())
    primary, secondary, offset = build(layout)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'gpt-primary.bin').write_bytes(primary)
    (args.out / 'gpt-secondary.bin').write_bytes(secondary)
    (args.out / 'gpt.json').write_text(json.dumps(dict(layout=layout, primary_bytes=len(primary),
        secondary_bytes=len(secondary), secondary_byte_offset=offset), indent=2) + '\n')
    print(json.dumps({'primary_bytes': len(primary), 'secondary_bytes': len(secondary),
                      'secondary_byte_offset': offset, 'disk_bytes': layout['disk_sectors'] * 512}))


if __name__ == '__main__':
    main()
