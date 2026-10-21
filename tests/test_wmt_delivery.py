# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Explicit WMT production-parser interoperability and payload v5 contracts."""
import contextlib,ctypes,hashlib,io,json,os,struct,subprocess,sys,tempfile,unittest,zlib
from pathlib import Path
from unittest import mock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import wmt_firmware as wmt
import build as image_build
from payload import HEADER,SEGMENT,PAGE,build
from test_payload import kernel_fixture
from test_rogue_firmware import fixture as gpu_fixture


def patch(sequence):
    raw=bytearray(28+37+sequence)
    raw[:16]=bytes(range(16));raw[16:20]=b'TEST'
    struct.pack_into('>HH',raw,20,0x8a11,0x8a11);raw[24]=0x20|sequence;raw[26]=8
    for i in range(28,len(raw)):raw[i]=(i+sequence)%256
    return bytes(raw)


def bundle(directory):
    paths=[]
    for seq in (1,2):
        p=Path(directory)/f'mt6630_patch_unit_{seq}.bin';p.write_bytes(patch(seq));paths.append(p)
    return wmt.pack(paths)[0]


def tree(data,at=0):
    n,c=struct.unpack_from('<II',data,at);at+=8;props={};children=[]
    for _ in range(n):
        name=data[at:at+32].split(b'\0')[0];length=struct.unpack_from('<I',data,at+32)[0];at+=36
        props[name]=data[at:at+length];at+=(length+3)&~3
    for _ in range(c):child,at=tree(data,at);children.append(child)
    return (props,children),at


class WMTDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup);cls.work=Path(cls.temp.name)
        cls.raw=bundle(cls.work);shim=ROOT.parent/'bootshim';headers=ROOT.parent/'kernel/iokit/Kernel/arm'
        wrapper=cls.work/'contract.c';wrapper.write_text('''#include "handoff.h"
#include "sdio/A04BR3WMTFirmware.h"
#include "sdio/A04BR3WMTBootFirmware.h"
#include "rogue/A04BR3RogueBootFirmware.h"
int parse(const void *p,unsigned n) { struct a04_wmt_identity id={0x6630,0x8a12,0x8a11};struct a04_wmt_firmware_set set;return a04_wmt_firmware_bundle(&id,p,n,&set); }
size_t contract(const void *blob,size_t bytes,void *adt,size_t capacity) {
 struct fdt_info f={0};struct handoff_plan plan;
 f.bytes=0x10000;f.memory_count=1;f.memory[0].base=0x40000000;f.memory[0].size=0x40000000;
 if(handoff_validate(blob,bytes,0x47000000,&f,0x4e000000,0x40080000,0x400a0000,&plan))return 0;
 const struct xnu_payload *p=blob;
 if(p->version!=5)return 0;
 const struct xnu_segment *s=(const void *)((const unsigned char *)p+p->segments_offset);
 const struct xnu_segment *w=&s[p->segment_count-2],*g=&s[p->segment_count-1];
 if(!a04_wmt_boot_firmware_range(w->physical,w->file_size,g->physical,g->file_size,p->phys_base,p->kernel_end_pa-p->phys_base,p->phys_base,p->mem_size,plan.top))return 0;
 if(!a04_rgx_boot_firmware_range(g->physical,g->file_size,p->phys_base,p->kernel_end_pa-p->phys_base,p->phys_base,p->mem_size,plan.top))return 0;
 if(parse((const unsigned char *)p+w->file_offset,w->file_size))return 0;
 return xnu_make_adt(adt,capacity,p,13000000);
}
''')
        libpath=cls.work/'contract.so'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-DHANDOFF_HOST_TEST','-I',str(shim),'-I',str(headers),str(wrapper),str(shim/'handoff.c'),str(shim/'adt.c'),'-o',str(libpath)],check=True)
        cls.lib=ctypes.CDLL(str(libpath));cls.lib.parse.argtypes=[ctypes.c_void_p,ctypes.c_uint]
        cls.lib.contract.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t];cls.lib.contract.restype=ctypes.c_size_t

    def contract(self,blob):
        adt=ctypes.create_string_buffer(PAGE);n=self.lib.contract(ctypes.create_string_buffer(bytes(blob)),len(blob),adt,PAGE)
        return n,adt.raw[:n]

    def test_packer_matches_production_and_reversed_explicit_order(self):
        self.assertEqual(self.lib.parse(self.raw,len(self.raw)),0)
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for seq in (2,1):
                p=Path(d)/f'mt6630_patch_unit_{seq}.bin';p.write_bytes(patch(seq));paths.append(p)
            blob,info=wmt.pack(paths)
            self.assertEqual([e['sequence'] for e in info['entries']],[1,2]);self.assertEqual(self.lib.parse(blob,len(blob)),0)
            with self.assertRaises(ValueError):wmt.pack(paths[:1])
            with self.assertRaises(ValueError):wmt.pack([paths[0],paths[0]])
            old=bytearray(patch(1));old[23]=0x10;paths[0].write_bytes(old)
            with self.assertRaises(ValueError):wmt.pack(paths)

    def test_bounds_crc_names_and_set_coherence(self):
        faults=[]
        for at,fmt,value in [(8,'<I',2),(12,'<I',0),(12,'<I',16),(16,'<I',1),(20,'<I',63),(24,'<I',31),(28,'<I',1),(80,'<I',159),(80,'<I',0xffffffff),(84,'<I',28),(84,'<I',0xffffffff),(92,'<I',1),(144,'<I',160)]:
            bad=bytearray(self.raw);struct.pack_into(fmt,bad,at,value);faults.append(bad)
        for at in (0,32,47,88,160,len(self.raw)-1):
            bad=bytearray(self.raw);bad[at]^=0x80;faults.append(bad)
        # Coherent CRC does not make a mixed release/duplicate sequence valid.
        off,size=struct.unpack_from('<II',self.raw,144)
        for delta in (0,16,20,22,24):
            bad=bytearray(self.raw);bad[off+delta]^=1;struct.pack_into('<I',bad,152,zlib.crc32(bad[off:off+size]));faults.append(bad)
        for bad in faults:
            with self.subTest(offsets=bad[:32]),self.assertRaises(ValueError):wmt.stage(bad)
            self.assertNotEqual(self.lib.parse(bytes(bad),len(bad)),0)
        for n in range(len(self.raw)):
            self.assertNotEqual(self.lib.parse(self.raw,n),0)
            with self.assertRaises(ValueError):wmt.stage(self.raw[:n])

    def test_v5_python_to_shim_adt_both_kernel_ranges(self):
        blob,info=build(kernel_fixture(),cmdline='-v rd=emmcroot0',gpu_firmware=gpu_fixture(),wmt_firmware=self.raw)
        h=HEADER.unpack_from(blob);self.assertEqual(h[1:3],(5,3))
        w=SEGMENT.unpack_from(blob,HEADER.size+SEGMENT.size);g=SEGMENT.unpack_from(blob,HEADER.size+2*SEGMENT.size)
        self.assertEqual(w[0]+w[1],g[0]);self.assertEqual(g[0]+g[1],info['kernel_end_pa'])
        self.assertEqual(blob[w[2]:w[2]+w[3]],self.raw);self.assertEqual(blob[g[2]:g[2]+g[3]],bytes(gpu_fixture()))
        self.assertEqual(w[1],(len(self.raw)+PAGE-1)//PAGE*PAGE)
        n,data=self.contract(blob);self.assertGreater(n,0)
        (props,children),end=tree(data);self.assertEqual(end,n)
        chosen=next(t for t in children if t[0][b'name']==b'chosen\0')
        memory=next(t[0] for t in chosen[1] if t[0][b'name']==b'memory-map\0')
        self.assertEqual(struct.unpack('<2Q',memory[b'WMTFirmware-A04BR3']),(w[0],len(self.raw)))
        self.assertEqual(struct.unpack('<2Q',memory[b'GPUFirmware-A04BR3']),(g[0],len(gpu_fixture())))
        self.assertNotIn(b'RAMDisk',memory)
        self.assertEqual(info['wmt_firmware']['sha256'],hashlib.sha256(self.raw).hexdigest())

    def test_v5_descriptor_entry_source_and_root_rejections(self):
        blob,info=build(kernel_fixture(),cmdline='rd=emmcroot0',gpu_firmware=gpu_fixture(),wmt_firmware=self.raw)
        w=SEGMENT.unpack_from(blob,720);g=SEGMENT.unpack_from(blob,752);va=HEADER.unpack_from(blob)[3]
        cases=[(8,'<I',6),(12,'<I',2),(48,'<Q',info['kernel_end_pa']-PAGE),(720,'<Q',w[0]+8),(720,'<Q',w[0]+PAGE),
               (728,'<Q',w[1]+PAGE),(736,'<Q',w[2]+8),(744,'<Q',31),(744,'<Q',w[3]-8),
               (752,'<Q',g[0]+PAGE),(760,'<Q',g[1]+PAGE),(768,'<Q',w[2]),(776,'<Q',4096),
               (40,'<Q',va+w[0]-0x60000000),(40,'<Q',va+g[0]-0x60000000)]
        for at,fmt,value in cases:
            bad=bytearray(blob);struct.pack_into(fmt,bad,at,value)
            with self.subTest(at=at,value=value):self.assertEqual(self.contract(bad)[0],0)
        for root in (b'',b'rd=md0',b'rd=emmcroot0 rd=md0',b'rd=emmcroot0 rd=emmcroot0',b'rd=emmcroot0\trd=md0'):
            bad=bytearray(blob);bad[80:688]=root.ljust(608,b'\0');self.assertEqual(self.contract(bad)[0],0)
        bad=bytearray(blob)+bytes(8);struct.pack_into('<Q',bad,72,len(bad));self.assertEqual(self.contract(bad)[0],0)
        self.assertEqual(self.contract(blob[:-1])[0],0)
        for options in ({'gpu_firmware':None},{'ramdisk':b'root'},{'compact_ramdisk':True},{'cmdline':'rd=md0'}):
            args=dict(cmdline='rd=emmcroot0',gpu_firmware=gpu_fixture(),wmt_firmware=self.raw);args.update(options)
            with self.assertRaises(ValueError):build(kernel_fixture(),**args)

    def test_cli_rejects_implicit_or_invalid_paths_before_build(self):
        for args in (['--wmt-firmware','/missing'],['--kernel','kernel','--emmc-root','--wmt-firmware','/missing'],['--kernel','kernel','--emmc-root','--gpu-firmware','/missing-gpu','--wmt-firmware','/missing']):
            with mock.patch.object(sys,'argv',['build.py',*args]),mock.patch.object(image_build.subprocess,'run') as run,contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):image_build.main()
            run.assert_not_called()

    def test_main_cli_routes_explicit_bundle_into_v5_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'sources').mkdir();shim=root/'shim-src';shim.mkdir()
            (shim/'build.sh').write_text('unit fixture')
            (root/'sources/bootshim.files.json').write_text(json.dumps({'build.sh':hashlib.sha256((shim/'build.sh').read_bytes()).hexdigest()}))
            (root/'sources.lock.json').write_text(json.dumps({'kernel':{'commit':'unit-kernel'},'bootshim':{'commit':'unit-shim'}}))
            (root/'board.json').write_text(json.dumps({'boot_image':{},'xnu_handoff':{'physical_base':'0x60000000','managed_size':'0x18000000','cmdline':'-v'}}))
            kernel=root/'kernel';kernel.write_bytes(kernel_fixture())
            provenance=root/'kernel.json';provenance.write_text(json.dumps({'full_kernel_built':True,'source_commit':'unit-kernel','kernel_sha256':hashlib.sha256(kernel.read_bytes()).hexdigest()}))
            gpu=root/'gpu';gpu.write_bytes(gpu_fixture());wmtpath=root/'wmt';wmtpath.write_bytes(self.raw)
            destination=root/'out'
            args=['build.py','--bootshim',str(shim),'--out',str(destination),'--kernel',str(kernel),'--kernel-build-json',str(provenance),'--emmc-root','--gpu-firmware',str(gpu),'--wmt-firmware',str(wmtpath)]
            def fake_run(command,**kw):
                if command[0]=='dtc':Path(command[command.index('-o')+1]).write_bytes(b'unit dtb')
                elif command[0]=='bash':
                    out=Path(kw['env']['SHIM_OUT']);out.mkdir();(out/'Image').write_bytes(b'unit shim')
                else:self.fail('unexpected subprocess '+repr(command))
            with mock.patch.object(sys,'argv',args),mock.patch.object(image_build,'ROOT',root),mock.patch.object(image_build,'git_revision',return_value=None),mock.patch.object(image_build,'output',return_value='unit tool'),mock.patch.object(image_build.subprocess,'run',side_effect=fake_run),mock.patch.object(image_build,'build_from_source',return_value=(b'unit image',{})) as package,contextlib.redirect_stdout(io.StringIO()):
                image_build.main()
            packed=package.call_args.args[3];self.assertEqual(HEADER.unpack_from(packed)[1:3],(5,3))
            self.assertGreater(self.contract(packed)[0],0)
            meta=json.loads((destination/'build.json').read_text())['kernel']
            self.assertEqual(meta['wmt_firmware']['input_path'],str(wmtpath.resolve()))
            self.assertEqual(meta['wmt_firmware']['sha256'],hashlib.sha256(self.raw).hexdigest())
            self.assertFalse(meta['wmt_firmware']['firmware_running'])

    def test_external_pinned_files_reproduce_reported_bundle(self):
        root=ROOT.parent.parent.parent
        patches=root/'CT3_GPL_LGPL_Src/combo_tool/patch_folder'
        paths=[patches/f'mt6630_patch_e3_{i}_hdr.bin' for i in (0,1)]
        if not all(p.is_file() for p in paths):self.skipTest('external reference source files not supplied')
        blob,meta=wmt.pack(paths)
        self.assertEqual(meta['source_sha256'],'d17364077b0c3517834079dc4bf2f36a941b7046ebee7af7cb1fa7ea0e0c057c')
        self.assertEqual(self.lib.parse(blob,len(blob)),0)
        packed,info=build(kernel_fixture(),cmdline='rd=emmcroot0',gpu_firmware=gpu_fixture(),wmt_firmware=blob)
        self.assertGreater(self.contract(packed)[0],0)
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'bundle'
            command=[sys.executable,str(ROOT/'tools/wmt_firmware.py'),'--out',str(out)]
            for path in paths:command+=['--patch',str(path)]
            first=subprocess.run(command,capture_output=True,text=True);self.assertEqual(first.returncode,0,first.stderr);self.assertEqual(out.read_bytes(),blob)
            self.assertNotEqual(subprocess.run(command,capture_output=True).returncode,0)

if __name__=='__main__':unittest.main()
