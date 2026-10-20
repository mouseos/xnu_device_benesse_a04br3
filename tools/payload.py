#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yuma shimizu
"""Pack an unencrypted, plain-arm64 source-built Mach-O kernel for the shim."""
import argparse
from pathlib import Path
import struct

PAGE = 0x4000
HEADER = struct.Struct('<8sII8Q608s')
SEGMENT = struct.Struct('<4Q')


def align(value, size):
    return (value + size - 1) & -size


def bounded(start, size, total):
    if start < 0 or size < 0 or start > total or size > total - start:
        raise ValueError('Mach-O range exceeds the file')


def macho(data):
    if len(data) < 32:
        raise ValueError('truncated Mach-O')
    magic, cpu, sub, kind, count, command_bytes, flags, reserved = struct.unpack_from('<8I', data)
    if magic != 0xfeedfacf or cpu != 0x100000c or sub & 0xffffff or kind != 2:
        raise ValueError('expected thin plain-arm64 executable Mach-O, not arm64e')
    bounded(32, command_bytes, len(data))
    if count > command_bytes // 8:
        raise ValueError('invalid load-command count')
    segments, entry, fixups = [], None, None
    at, end = 32, 32 + command_bytes
    for _ in range(count):
        bounded(at, 8, end)
        command, size = struct.unpack_from('<II', data, at)
        if size < 8 or size & 7:
            raise ValueError('invalid load-command size')
        bounded(at, size, end)
        if command == 0x19:
            if size < 72:
                raise ValueError('short segment command')
            name = data[at + 8:at + 24].split(b'\0')[0]
            va, mem, offset, length, maximum, protection, nsects, segflags = struct.unpack_from('<4Q4I', data, at + 24)
            if size != 72 + nsects * 80:
                raise ValueError('invalid segment or section count')
            bounded(offset, length, len(data))
            # XNU ctf_insert emits a file-only __CTF segment (vmsize zero).
            # It is debug metadata and must not be copied into guest memory.
            if length > mem and not (name == b'__CTF' and mem == 0):
                raise ValueError('file-backed segment exceeds virtual size')
            if mem:
                segments.append(dict(name=name, va=va, memory=mem, offset=offset,
                                     size=length, executable=bool(protection & 4)))
        elif command == 5:
            if entry is not None or size < 288:
                raise ValueError('invalid UNIXTHREAD command')
            flavor, words = struct.unpack_from('<II', data, at + 8)
            if flavor != 6 or words != 68:
                raise ValueError('unexpected ARM_THREAD_STATE64 layout')
            entry = struct.unpack_from('<Q', data, at + 16 + 32 * 8)[0]
        elif command == 0x80000034:
            if fixups is not None or size != 16:
                raise ValueError('invalid chained-fixups command')
            fixups = struct.unpack_from('<II', data, at + 8)
            bounded(*fixups, len(data))
        elif command in (0x22, 0x80000022):
            if size < 48 or any(struct.unpack_from('<8I', data, at + 8)):
                raise ValueError('classic rebase/bind opcodes are not supported')
        at += size
    if at != end or not 1 <= len(segments) <= 16 or entry is None:
        raise ValueError('missing kernel segments or entry')
    if segments[0]['name'] != b'__TEXT' or segments[0]['offset'] != 0:
        raise ValueError('expected __TEXT with Mach header at file offset zero')
    if sum(s['executable'] and s['va'] <= entry and entry + 4 <= s['va'] + s['size'] for s in segments) != 1:
        raise ValueError('entry is not in one file-backed executable segment')
    for i, segment in enumerate(segments):
        if segment['va'] + segment['memory'] > 1 << 64:
            raise ValueError('virtual segment overflow')
        for other in segments[:i]:
            if segment['va'] < other['va'] + other['memory'] and other['va'] < segment['va'] + segment['memory']:
                raise ValueError('overlapping virtual segments')
    return segments, entry, fixups


def resolve_chains(data, segments, fixups):
    """Perform link/load rebasing of the source kernel; reject binds/PAC."""
    if fixups is None:
        return 0
    origin, length = fixups
    bounded(origin, 28, len(data))
    version, starts, imports, symbols, imports_count, imports_format, symbols_format = struct.unpack_from('<7I', data, origin)
    if version or imports_count:
        raise ValueError('unsupported fixups version or imported bindings')
    bounded(starts, 4, length)
    count = struct.unpack_from('<I', data, origin + starts)[0]
    if count != len(segments):
        raise ValueError('fixup segment inventory differs from Mach-O')
    bounded(starts + 4, count * 4, length)
    fixed = 0
    image_base = segments[0]['va']
    for i, segment in enumerate(segments):
        relative = struct.unpack_from('<I', data, origin + starts + 4 + 4 * i)[0]
        if not relative:
            continue
        pos = starts + relative
        bounded(pos, 22, length)
        size, page, fmt, vmoffset, max_valid, page_count = struct.unpack_from('<IHHQIH', data, origin + pos)
        if size < 22 + 2 * page_count or page not in (4096, 16384) or fmt not in (2, 6, 8):
            raise ValueError('unsupported chained-pointer layout (including arm64e/PAC)')
        bounded(pos, size, length)
        if vmoffset != segment['va'] - image_base:
            raise ValueError('fixup virtual offset differs from segment')
        for p in range(page_count):
            start = struct.unpack_from('<H', data, origin + pos + 22 + 2 * p)[0]
            if start == 0xffff:
                continue
            if start & 0x8000 or start >= page:
                raise ValueError('multi-start chains are unsupported')
            offset = p * page + start
            while True:
                if offset + 8 > segment['size'] or offset // page != p:
                    raise ValueError('fixup escapes its file-backed page')
                location = segment['offset'] + offset
                raw = struct.unpack_from('<Q', data, location)[0]
                if raw >> 63:
                    raise ValueError('bind or authenticated pointer is unsupported')
                if fmt in (2, 6):
                    target = raw & ((1 << 36) - 1)
                    if fmt == 6:
                        target += image_base
                    target |= ((raw >> 36) & 0xff) << 56
                else:
                    if (raw >> 30) & 3:
                        raise ValueError('nonzero kernel-cache level is unsupported')
                    target = image_base + (raw & ((1 << 30) - 1))
                struct.pack_into('<Q', data, location, target)
                fixed += 1
                step = (raw >> 51) & 0xfff
                if not step:
                    break
                offset += step * 4
    return fixed


def build(kernel, phys_base=0x60000000, mem_size=0x18000000,
          cmdline='-v serial=3 debug=0x14e keepsyms=1 cpus=1', ramdisk=None):
    data = bytearray(kernel)
    segments, entry, fixups = macho(data)
    resolved = resolve_chains(data, segments, fixups)
    virtual = segments[0]['va'] & ~0x1ffffff
    if virtual < 0xffffff8000000000 or phys_base & 0x1ffffff or not mem_size or mem_size & (PAGE - 1):
        raise ValueError('invalid kernel virtual/physical memory window')
    command = cmdline.encode('ascii')
    if len(command) >= 608 or b'\0' in command:
        raise ValueError('invalid kernel command line')
    segment_end = max(s['va'] + s['memory'] for s in segments)
    end_pa = align(phys_base + segment_end - virtual, PAGE)
    if end_pa + 2 * PAGE > phys_base + mem_size:
        raise ValueError('kernel plus ADT/boot_args exceeds the managed window')
    if ramdisk is not None and not ramdisk:
        raise ValueError('empty root RAMDisk')
    count = len(segments) + (ramdisk is not None)
    if count > 16:
        raise ValueError('too many payload segments')
    offset = align(HEADER.size + SEGMENT.size * count, 64)
    payload = bytearray(offset)
    for i, s in enumerate(segments):
        if s['va'] < virtual or s['va'] - virtual + s['memory'] > mem_size:
            raise ValueError('kernel segment outside managed memory')
        destination = phys_base + s['va'] - virtual
        SEGMENT.pack_into(payload, HEADER.size + i * SEGMENT.size,
                          destination, s['memory'], len(payload), s['size'])
        payload += data[s['offset']:s['offset'] + s['size']]
        payload += bytes(-len(payload) % 8)
    ramdisk_info = None
    if ramdisk is not None:
        rd_size = align(len(ramdisk), PAGE)
        if end_pa + rd_size + 2 * PAGE > phys_base + mem_size:
            raise ValueError('RAMDisk plus ADT/boot_args exceeds managed memory')
        SEGMENT.pack_into(payload, HEADER.size + len(segments) * SEGMENT.size,
                          end_pa, rd_size, len(payload), len(ramdisk))
        ramdisk_info = dict(physical=end_pa, size=rd_size, file_size=len(ramdisk))
        payload += ramdisk
        payload += bytes(-len(payload) % 8)
        end_pa += rd_size
    HEADER.pack_into(payload, 0, b'A04XNU01', 2 if ramdisk is not None else 1, count, virtual, phys_base,
                     mem_size, entry, end_pa, HEADER.size, offset, len(payload), command)
    if len(payload) > 0x8000000:
        raise ValueError('payload exceeds the 128 MiB loader limit')
    return bytes(payload), dict(chained_rebases=resolved, segments=len(segments),
                               virt_base=virtual, phys_base=phys_base, mem_size=mem_size,
                               entry_va=entry, kernel_end_pa=end_pa, ramdisk=ramdisk_info)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    payload, info = build(args.kernel.read_bytes())
    args.output.write_bytes(payload)
    print(info)


if __name__ == '__main__':
    main()
