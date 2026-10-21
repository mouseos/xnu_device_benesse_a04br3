# SPDX-License-Identifier: BSD-2-Clause-Patent
import contextlib
import ctypes
import subprocess
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import build as image_build
from payload import HEADER, SEGMENT, PAGE, build
from test_payload import kernel_fixture
from test_rogue_firmware import fixture


class GPUFirmwareDelivery(unittest.TestCase):
    def test_v4_raw_tail_actual_size_hash_and_reserved_end(self):
        firmware = bytes(fixture())
        plain, plain_info = build(kernel_fixture())
        blob, info = build(kernel_fixture(), cmdline='-v rd=emmcroot0', gpu_firmware=firmware)
        h = HEADER.unpack_from(blob)
        self.assertEqual(HEADER.size, 688)
        self.assertEqual(h[:3], (b'A04XNU01', 4, 2))
        pa, memory, offset, length = SEGMENT.unpack_from(blob, HEADER.size+SEGMENT.size)
        self.assertEqual(pa, plain_info['kernel_end_pa'])
        self.assertEqual((memory, length), (PAGE, len(firmware)))
        self.assertEqual(blob[offset:offset+length], firmware)
        self.assertEqual(offset+(length+7)//8*8, len(blob))
        self.assertEqual(h[7], pa+memory)
        self.assertEqual(info['gpu_firmware']['sha256'], hashlib.sha256(firmware).hexdigest())
        self.assertIsNone(info['ramdisk'])
        self.assertFalse(info['gpu_firmware']['firmware_running'])
        self.assertNotIn('gpu_firmware', plain_info)
        self.assertEqual(HEADER.unpack_from(plain)[1], 1)

    def test_exclusive_root_and_firmware_parser_bounds(self):
        for args in ({}, {'cmdline': 'rd=md0'}, {'cmdline': 'rd=emmcroot0 rd=md0'},
                     {'cmdline': 'rd=emmcroot00'}, {'cmdline': 'rd=emmcroot0', 'ramdisk': b'root'},
                     {'cmdline': 'rd=emmcroot0', 'compact_ramdisk': True}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                build(kernel_fixture(), gpu_firmware=fixture(), **args)
        for bad in (b'', bytes(4096), bytes(32*1024*1024+1)):
            with self.assertRaises(ValueError):
                build(kernel_fixture(), cmdline='rd=emmcroot0', gpu_firmware=bad)
        bad = fixture();struct.pack_into('<I', bad, 4096, 2)
        with self.assertRaises(ValueError):
            build(kernel_fixture(), cmdline='rd=emmcroot0', gpu_firmware=bad)
        with self.assertRaises(ValueError):
            build(kernel_fixture(), mem_size=0x1014000, cmdline='rd=emmcroot0', gpu_firmware=fixture())

    def test_explicit_cli_validation_before_any_image_build(self):
        for args in (['--gpu-firmware', '/not/a/default'],
                     ['--kernel', 'kernel', '--gpu-firmware', '/not/a/default'],
                     ['--kernel', 'kernel', '--emmc-root', '--gpu-firmware', '/not/a/default']):
            with mock.patch.object(sys, 'argv', ['build.py', *args]), \
                 mock.patch.object(image_build.subprocess, 'run') as run, \
                 contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                image_build.main()
            run.assert_not_called()

    def test_explicit_path_loader_and_main_packaging_without_build(self):
        # Exercise real build.main argument plumbing, with dtc/shim compilation
        # and image packaging replaced. Everything is a temporary test fixture.
        for with_gpu in (False, True):
            with self.subTest(with_gpu=with_gpu), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'sources').mkdir();shim=root/'shim-src';shim.mkdir()
                (shim/'build.sh').write_text('test source\n')
                (root/'sources/bootshim.files.json').write_text(json.dumps(
                    {'build.sh': hashlib.sha256((shim/'build.sh').read_bytes()).hexdigest()}))
                lock={'kernel': {'commit': 'test-kernel'}, 'bootshim': {'commit': 'test-shim'}}
                (root/'sources.lock.json').write_text(json.dumps(lock))
                (root/'board.json').write_text(json.dumps({'boot_image': {}, 'xnu_handoff': {
                    'physical_base': '0x60000000', 'managed_size': '0x18000000', 'cmdline': '-v cpus=1'}}))
                kernel=root/'kernel';kernel.write_bytes(kernel_fixture())
                provenance=root/'kernel.json';provenance.write_text(json.dumps({
                    'full_kernel_built': True, 'source_commit': 'test-kernel',
                    'kernel_sha256': hashlib.sha256(kernel.read_bytes()).hexdigest()}))
                fw=root/'explicit-fw';fw.write_bytes(fixture())
                destination=root/'out'
                args=['build.py', '--bootshim', str(shim), '--out', str(destination),
                      '--kernel', str(kernel), '--kernel-build-json', str(provenance), '--emmc-root']
                if with_gpu: args+=['--gpu-firmware', str(fw)]
                def fake_run(command, **kwargs):
                    if command[0]=='dtc': Path(command[command.index('-o')+1]).write_bytes(b'dtb')
                    elif command[0]=='bash':
                        out=Path(kwargs['env']['SHIM_OUT']);out.mkdir();(out/'Image').write_bytes(b'shim')
                    else: self.fail('unexpected subprocess '+repr(command))
                with mock.patch.object(sys, 'argv', args), mock.patch.object(image_build, 'ROOT', root), \
                     mock.patch.object(image_build, 'git_revision', return_value=None), \
                     mock.patch.object(image_build, 'output', return_value='tool 1.0'), \
                     mock.patch.object(image_build.subprocess, 'run', side_effect=fake_run), \
                     mock.patch.object(image_build, 'load_gpu_firmware', wraps=image_build.load_gpu_firmware) as loader, \
                     mock.patch.object(image_build, 'build_from_source', return_value=(b'test-image', {})) as package, \
                     contextlib.redirect_stdout(io.StringIO()):
                    image_build.main()
                self.assertEqual(loader.call_count, int(with_gpu))
                packed=package.call_args.args[3]
                self.assertEqual(HEADER.unpack_from(packed)[1], 4 if with_gpu else 1)
                result=json.loads((destination/'build.json').read_text())['kernel']
                if with_gpu:
                    self.assertEqual(result['gpu_firmware']['sha256'], hashlib.sha256(fw.read_bytes()).hexdigest())
                else: self.assertNotIn('gpu_firmware', result)

    def test_python_payload_to_c_handoff_adt_and_kernel_range_contract(self):
        shim=ROOT.parent/'bootshim'
        headers=ROOT.parent/'kernel/iokit/Kernel/arm/rogue'
        if not (shim/'handoff.c').is_file() or not headers.is_dir():
            self.skipTest('sibling bootshim/kernel sources required')
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);wrapper=work/'contract.c'
            wrapper.write_text('''#include "handoff.h"
#include "A04BR3RogueBootFirmware.h"
#include "A04BR3RogueFirmware.h"
size_t contract(const void *blob,size_t bytes,void *adt,size_t capacity,uint64_t *values) {
 struct fdt_info f={0};struct handoff_plan plan;
 f.bytes=0x10000;f.memory_count=1;f.memory[0].base=0x40000000;f.memory[0].size=0x40000000;
 if(handoff_validate(blob,bytes,0x47000000,&f,0x4e000000,0x40080000,0x400a0000,&plan))return 0;
 const struct xnu_payload *p=blob;
 const struct xnu_segment *s=(const void *)((const unsigned char *)p+p->segments_offset);
 s+=p->segment_count-1;
 if(!a04_rgx_boot_firmware_range(s->physical,s->file_size,p->phys_base,
     p->kernel_end_pa-p->phys_base,p->phys_base,p->mem_size,plan.top))return 0;
 struct a04_rgx_fw_layout layout;
 if(a04_rgx_fw_parse((const unsigned char *)p+s->file_offset,s->file_size,&layout))return 0;
 values[0]=plan.top;values[1]=layout.code_bytes;values[2]=layout.data_bytes;
 return xnu_make_adt(adt,capacity,p,13000000);
}
''')
            libpath=work/'contract.so'
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                            '-DHANDOFF_HOST_TEST','-I',str(shim),'-I',str(headers),
                            str(wrapper),str(shim/'handoff.c'),str(shim/'adt.c'),
                            '-o',str(libpath)],check=True)
            lib=ctypes.CDLL(str(libpath))
            lib.contract.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,
                                    ctypes.c_size_t,ctypes.POINTER(ctypes.c_uint64)]
            lib.contract.restype=ctypes.c_size_t
            firmware=[bytes(fixture())]
            real=Path(os.environ.get('A04_RGX_FIRMWARE',str(ROOT.parent.parent.parent/
                'port/gsi/vendor/benesse/a04br3/proprietary/vendor/firmware/rgx.fw.22.40.54.30')))
            if real.is_file(): firmware.append(real.read_bytes())
            for raw in firmware:
                packed,info=build(kernel_fixture(),cmdline='rd=emmcroot0',gpu_firmware=raw)
                adt=ctypes.create_string_buffer(PAGE);values=(ctypes.c_uint64*3)()
                n=lib.contract(ctypes.create_string_buffer(packed),len(packed),adt,PAGE,values)
                self.assertGreater(n,0)
                self.assertEqual(values[0],info['kernel_end_pa']+2*PAGE)
                def node(offset):
                    count,children=struct.unpack_from('<II',adt.raw,offset);offset+=8
                    props={};nested=[]
                    for _ in range(count):
                        name=adt.raw[offset:offset+32].split(b'\0')[0]
                        size=struct.unpack_from('<I',adt.raw,offset+32)[0];offset+=36
                        props[name]=adt.raw[offset:offset+size];offset+=(size+3)&~3
                    for _ in range(children):
                        child,offset=node(offset);nested.append(child)
                    return (props,nested),offset
                tree,end=node(0);self.assertEqual(end,n)
                chosen=next(child for child in tree[1] if child[0][b'name']==b'chosen\0')
                memory=next(child[0] for child in chosen[1] if child[0][b'name']==b'memory-map\0')
                self.assertEqual(struct.unpack('<2Q',memory[b'GPUFirmware-A04BR3']),
                                 (info['gpu_firmware']['physical'],len(raw)))
                self.assertNotIn(b'RAMDisk',memory)

    def test_external_real_blob_payload_is_unchanged(self):
        path=Path(os.environ.get('A04_RGX_FIRMWARE', str(ROOT.parent.parent.parent/
            'port/gsi/vendor/benesse/a04br3/proprietary/vendor/firmware/rgx.fw.22.40.54.30')))
        if not path.is_file():
            if 'A04_RGX_FIRMWARE' in os.environ: self.fail('explicit firmware missing')
            self.skipTest('external firmware absent')
        firmware, manifest=image_build.load_gpu_firmware(path)
        blob, info=build(kernel_fixture(), cmdline='rd=emmcroot0', gpu_firmware=firmware)
        pa, size, offset, length=SEGMENT.unpack_from(blob, HEADER.size+SEGMENT.size)
        self.assertEqual(blob[offset:offset+length], path.read_bytes())
        self.assertEqual(length, len(firmware))
        self.assertEqual(size, (length+PAGE-1)//PAGE*PAGE)
        self.assertEqual(info['gpu_firmware']['sha256'], manifest['source_sha256'])
        self.assertEqual(info['kernel_end_pa'], pa+size)
