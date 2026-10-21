#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Explicit external A04WMT1 packing/validation; no embedded firmware or search."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

LIMIT = 32 * 1024 * 1024
PATCH_LIMIT = 16 * 1024 * 1024 + 28
MAGIC = b'A04WMT1\0'


def _file(name, data, hardware, rom):
    if not re.fullmatch(rb'mt6630_patch[A-Za-z0-9_.-]*', name) or len(name) >= 48:
        raise ValueError('invalid ASCII patch basename')
    if not 28 < len(data) <= PATCH_LIMIT:
        raise ValueError('patch length outside production bounds')
    hw, sw = struct.unpack_from('>HH', data, 20)
    if (sw ^ rom) & 255 or (hw & 0xf0) != (hardware & 0xf0):
        raise ValueError('patch incompatible with explicit hardware/ROM identity')
    count, sequence = data[24] >> 4, data[24] & 15
    address = int.from_bytes(b'\0' + data[25:28], 'little')
    if not count or not sequence or sequence > count or len(data)-29 > 0xffffffff-address:
        raise ValueError('invalid multipatch count/sequence/destination')
    return dict(name=name.decode('ascii'), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                crc32=f'{zlib.crc32(data):08x}', hardware=hw, software=sw,
                count=count, sequence=sequence, destination=address,
                date=data[:16].hex(), platform=data[16:20].hex())


def stage(blob, *, chip=0x6630, hardware=0x8a12, rom=0x8a11):
    """Mirror the production reader's external set checks, return provenance."""
    if chip != 0x6630 or not 0 <= hardware <= 0xffff or not 0 <= rom <= 0xffff:
        raise ValueError('invalid chip/hardware/ROM')
    if not any((hardware & 0xf0) == (row & 0xf0) for row in (0x8a00,0x8a10,0x8a11,0x8b11)):
        raise ValueError('unsupported hardware major')
    if not 32 <= len(blob) <= LIMIT:
        raise ValueError('bundle length outside production bounds')
    magic, version, count, total, entry, header, flags = struct.unpack_from('<8s6I', blob)
    if magic != MAGIC or version != 1 or not 1 <= count <= 15 or total != len(blob) or (entry,header,flags)!=(64,32,0):
        raise ValueError('invalid A04WMT1 header')
    table_end = 32 + count * 64
    if table_end > len(blob):
        raise ValueError('truncated descriptor table')
    entries, ranges, names, sequences = [], [], set(), set()
    for i in range(count):
        descriptor = blob[32+i*64:96+i*64]
        field = bytes(descriptor[:48])
        if b'\0' not in field:
            raise ValueError('unterminated name')
        name, tail = field.split(b'\0',1)
        if any(tail):
            raise ValueError('nonzero name tail')
        off, size, crc, reserved = struct.unpack_from('<4I',descriptor,48)
        if reserved or off < table_end or off > len(blob) or not 28 < size <= PATCH_LIMIT or size > len(blob)-off:
            raise ValueError('invalid descriptor bounds')
        if name in names or any(off < start+n and start < off+size for start,n in ranges):
            raise ValueError('duplicate name or overlapping input')
        raw = blob[off:off+size]
        if zlib.crc32(raw) != crc:
            raise ValueError('patch CRC mismatch')
        info = _file(name, raw, hardware, rom)
        if info['count'] != count or info['sequence'] in sequences:
            raise ValueError('missing/duplicate sequence or wrong set count')
        if entries and any(info[k] != entries[0][k] for k in ('hardware','software','date','platform')):
            raise ValueError('mixed patch release')
        info['offset'] = off
        entries.append(info); ranges.append((off,size)); names.add(name); sequences.add(info['sequence'])
    if sequences != set(range(1,count+1)):
        raise ValueError('incomplete multipatch set')
    return dict(format='A04WMT1', bytes=len(blob), source_sha256=hashlib.sha256(blob).hexdigest(),
                chip=chip, hardware=hardware, rom=rom, entries=sorted(entries,key=lambda e:e['sequence']),
                firmware_running=False)


def load(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(LIMIT+1)
    return raw, stage(raw)


def pack(paths):
    """Require the explicit two-file MT6630 set; caller order is preserved."""
    if len(paths) != 2:
        raise ValueError('explicitly select exactly two matching external patches')
    files=[]
    for path in map(Path, paths):
        with path.open('rb') as stream:
            raw=stream.read(PATCH_LIMIT+1)
        _file(path.name.encode('ascii'),raw,0x8a12,0x8a11)
        files.append((path,raw))
    total=32+64*len(files)+sum(len(raw) for _,raw in files)
    if total>LIMIT:
        raise ValueError('bundle too large')
    result=bytearray(total)
    struct.pack_into('<8s6I',result,0,MAGIC,1,len(files),total,64,32,0)
    off=32+64*len(files)
    for i,(path,raw) in enumerate(files):
        name=path.name.encode('ascii');result[32+64*i:32+64*i+len(name)]=name
        struct.pack_into('<4I',result,32+64*i+48,off,len(raw),zlib.crc32(raw),0)
        result[off:off+len(raw)]=raw;off+=len(raw)
    blob=bytes(result);manifest=stage(blob)
    for entry in manifest['entries']:
        entry['source']=str(next(path for path,_ in files if path.name==entry['name']).resolve())
    return blob,manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch',type=Path,action='append',required=True,help='Explicit file; repeat exactly twice')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        blob,manifest=pack(args.patch)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    with args.out.open('xb') as stream: stream.write(blob)
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
