#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 yuma shimizu
"""A04BR3 Android v0 / MediaTek boot image construction. No device access."""
import argparse
import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path

MTK_MAGIC = 0x58881688
PARTITION_SIZE = 128 * 1024 * 1024
REQUIRED_BOOTOPT = b'bootopt=64S3,32N2,64N2'


def checked_cmdline(value):
    data = value.encode('ascii')
    # MT8167 preloader reads offset 0x40 directly, before LK loads our payload.
    # An absent prefix selects 64S3,32S1,32S1 instead of the working NS boot.
    if data.split(b' ', 1)[0] != REQUIRED_BOOTOPT or len(data) >= 512:
        raise ValueError('A04BR3 command line must start with bootopt=64S3,32N2,64N2')
    return data


def pad(data, alignment):
    return data + bytes(-len(data) % alignment)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def section(header, payload):
    result = bytearray(header)
    struct.pack_into('<I', result, 4, len(payload))
    return bytes(result) + payload


def parse(image):
    if len(image) < 2048 or image[:8] != b'ANDROID!':
        raise ValueError('expected Android boot header')
    fields = struct.unpack_from('<10I', image, 8)
    ksize, kaddr, rsize, raddr, ssize, saddr, tags, page, version, osver = fields
    if version != 0 or ssize != 0 or page != 2048 or kaddr != 0x40080000:
        raise ValueError('expected A04BR3 v0, 2048-byte pages, kernel at 0x40080000, no second stage')
    roff = page + ((ksize + page - 1) // page) * page
    if ksize < 512 or rsize < 512 or roff + rsize > len(image):
        raise ValueError('truncated boot image sections')
    sections = (image[page:page + ksize], image[roff:roff + rsize])
    for data, expected in zip(sections, (b'KERNEL', b'ROOTFS')):
        magic, length, name = struct.unpack_from('<II32s', data)
        if magic != MTK_MAGIC or length != len(data) - 512 or name.rstrip(b'\0') != expected:
            raise ValueError('invalid MediaTek section header or length')
    digest = hashlib.sha1()
    for data in (*sections, b''):
        digest.update(data)
        digest.update(struct.pack('<I', len(data)))
    # Some historical stock images have stale IDs. Report instead of rejecting
    # the input; newly generated images must have a matching ID.
    return fields, sections, image[576:608] == digest.digest().ljust(32, b'\0')


def split_kernel(payload):
    stream = zlib.decompressobj(31)
    raw = stream.decompress(payload, 64 * 1024 * 1024)
    if not stream.eof or stream.unconsumed_tail:
        raise ValueError('kernel is not a complete gzip stream within 64 MiB')
    consumed = len(payload) - len(stream.unused_data)
    trailing = stream.unused_data
    offset = len(trailing) - len(trailing.lstrip(b'\0'))
    dtb = trailing[offset:]
    if len(dtb) < 40 or dtb[:4] != b'\xd0\x0d\xfe\xed':
        raise ValueError('missing DTB immediately after gzip and optional zero alignment')
    total = struct.unpack_from('>I', dtb, 4)[0]
    if total != len(dtb):
        raise ValueError('DTB length mismatch or multiple appended blobs')
    return raw, dtb, consumed


def gzip_deterministic(data):
    # gzip -n excludes input name/time; the gzip version is captured in manifest.
    return subprocess.run(['gzip', '-n', '-9', '-c'], input=data,
                          stdout=subprocess.PIPE, check=True).stdout


def empty_cpio():
    name = b'TRAILER!!!\0'
    values = [0] * 13
    values[4] = 1  # nlink
    values[11] = len(name)
    return pad(pad(b'070701' + b''.join(f'{n:08x}'.encode() for n in values) + name, 4), 512)


def source_template(dtb, config):
    """Construct all framing from tracked configuration, without a boot dump."""
    if config['header_version'] != 0 or config['kernel_gzip_alignment'] != 512:
        raise ValueError('unsupported board boot configuration')
    parts = []
    for name, payload in ((b'KERNEL', gzip_deterministic(b'') + dtb),
                          (b'ROOTFS', gzip_deterministic(empty_cpio()))):
        header = struct.pack('<II32s', MTK_MAGIC, 0, name)
        header += bytes([config['mtk_header_fill']]) * 472
        parts.append(section(header, payload))
    page = config['page_bytes']
    if page != 2048:
        raise ValueError('unsupported boot page size')
    header = bytearray(page)
    header[:8] = b'ANDROID!'
    cmdline = checked_cmdline(config.get('cmdline', ''))
    header[64:64 + len(cmdline)] = cmdline
    struct.pack_into('<10I', header, 8, len(parts[0]), int(config['kernel_addr'], 0),
                     len(parts[1]), int(config['ramdisk_addr'], 0), 0,
                     int(config['second_addr'], 0), int(config['tags_addr'], 0),
                     page, 0, 0)
    return bytes(header) + b''.join(pad(part, page) for part in parts)


def build_from_source(dtb, shim, config, kernel_payload=None):
    image, manifest = build(source_template(dtb, config), shim, kernel_payload)
    del manifest['template_sha256']
    del manifest['template_id_valid']
    manifest['input_kind'] = 'tracked DTS and board configuration'
    return image, manifest


def build(template, shim, kernel_payload=None):
    fields, (old_kernel, old_ramdisk), input_id_ok = parse(template)
    _, dtb, _ = split_kernel(old_kernel[512:])
    if len(shim) < 64 or shim[56:60] != b'ARM\x64':
        raise ValueError('missing arm64 Image header')
    if struct.unpack_from('<Q', shim, 16)[0] != len(shim) or len(shim) > 0x1000000:
        raise ValueError('invalid shim Image size')
    kernel = section(old_kernel[:512], pad(gzip_deterministic(shim), 512) + dtb)
    if kernel_payload is not None and (len(kernel_payload) > PARTITION_SIZE or
            kernel_payload[:8] not in (b'A04XNU01',)):
        raise ValueError('invalid or oversized XNU payload')
    ramdisk = section(old_ramdisk[:512], gzip_deterministic(empty_cpio()) if kernel_payload is None else kernel_payload)
    # Fresh Android header: retain the explicitly configured preloader bootopt
    # and load addresses, clear vendor extension data and Android OS version.
    header = bytearray(fields[7])
    header[:8] = b'ANDROID!'
    new_fields = list(fields)
    new_fields[0], new_fields[2], new_fields[9] = len(kernel), len(ramdisk), 0
    struct.pack_into('<10I', header, 8, *new_fields)
    header[48:64] = b'xnu-a04br3'.ljust(16, b'\0')
    cmdline = checked_cmdline(template[64:576].split(b'\0', 1)[0].decode('ascii'))
    header[64:64 + len(cmdline)] = cmdline
    digest = hashlib.sha1()
    for data in (kernel, ramdisk, b''):
        digest.update(data)
        digest.update(struct.pack('<I', len(data)))
    header[576:608] = digest.digest().ljust(32, b'\0')
    image = bytes(header) + pad(kernel, fields[7]) + pad(ramdisk, fields[7])
    if len(image) > PARTITION_SIZE:
        raise ValueError('image exceeds 128 MiB boot partition')
    _, (check_kernel, check_ramdisk), id_ok = parse(image)
    raw, check_dtb, compressed_size = split_kernel(check_kernel[512:])
    if not id_ok or raw != shim or check_dtb != dtb:
        raise ValueError('generated image did not round-trip')
    if check_kernel[512 + ((compressed_size + 511) // 512) * 512:] != dtb:
        raise ValueError('DTB is not at the required 512-byte boundary')
    if kernel_payload is None:
        if zlib.decompress(check_ramdisk[512:], 31) != empty_cpio():
            raise ValueError('ramdisk did not round-trip')
    elif check_ramdisk[512:] != kernel_payload:
        raise ValueError('XNU payload did not round-trip')
    manifest = dict(kind='a04br3-diagnostic-boot', template_sha256=sha256(template),
                    template_id_valid=input_id_ok, shim_sha256=sha256(shim),
                    dtb_sha256=sha256(dtb), boot_sha256=sha256(image),
                    boot_bytes=len(image), kernel_load_address=fields[1],
                    page_bytes=fields[7], mtk_header_bytes=512,
                    preloader_bootopt=REQUIRED_BOOTOPT.decode('ascii'),
                    kernel_payload_present=kernel_payload is not None,
                    hardware_tested=False, xnu_boot_verified=False)
    return image, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--board', type=Path, default=Path(__file__).resolve().parents[1] / 'board.json')
    parser.add_argument('--shim', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    image, manifest = build_from_source(args.dtb.read_bytes(), args.shim.read_bytes(),
                                        json.loads(args.board.read_text())['boot_image'])
    manifest['gzip_version'] = subprocess.check_output(['gzip', '--version'], text=True).splitlines()[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    args.output.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
