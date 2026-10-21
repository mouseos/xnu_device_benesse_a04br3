# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Real Python MTKW/v6 -> production C parser, shim, ADT and range contracts."""
from pathlib import Path
import ctypes,struct,subprocess,tempfile,unittest,zlib,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import wlan_firmware
from payload import build,HEADER,SEGMENT,PAGE
from test_payload import kernel_fixture
from test_rogue_firmware import fixture as gpu_fixture
from test_wmt_delivery import bundle,tree

def firmware():
    b=bytearray(48+73)
    struct.pack_into('<4I',b,0,0x574b544d,0,2,0)
    struct.pack_into('<4I',b,16,48,0,37,0x9000)
    struct.pack_into('<4I',b,32,85,0,36,0xa000)
    b[48:]=bytes(range(73));struct.pack_into('<I',b,4,zlib.crc32(b[8:]));return bytes(b)

class WLANDelivery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup);d=Path(cls.temp.name)
        cls.wmt=bundle(d);shim=ROOT.parent/'bootshim';headers=ROOT.parent/'kernel/iokit/Kernel/arm'
        (d/'contract.c').write_text('''#include "handoff.h"
#include "sdio/A04BR3WLANFirmware.h"
#include "sdio/A04BR3WLANBootFirmware.h"
int parse(const void *p,unsigned n){struct a04_wlan_firmware f;return a04_wlan_firmware_parse(p,n,&f);}
size_t contract(const void *blob,size_t bytes,void *adt,size_t cap){
 struct fdt_info f={0};struct handoff_plan plan;f.bytes=65536;f.memory_count=1;
 f.memory[0].base=0x40000000;f.memory[0].size=0x40000000;
 if(handoff_validate(blob,bytes,0x47000000,&f,0x4e000000,0x40080000,0x400a0000,&plan))return 0;
 const struct xnu_payload *p=blob;if(p->version!=6)return 0;
 const struct xnu_segment *s=(const void *)((const unsigned char *)p+p->segments_offset);
 const struct xnu_segment *w=&s[p->segment_count-3],*m=w+1,*g=w+2;
 uint64_t pairs[4][2]={{w->physical,w->file_size},{m->physical,m->file_size},{g->physical,g->file_size},{p->phys_base,p->kernel_end_pa-p->phys_base}};
 if(!a04_wlan_boot_firmware_range(pairs,p->phys_base,p->mem_size,plan.top))return 0;
 if(parse((const unsigned char *)p+w->file_offset,w->file_size))return 0;
 return xnu_make_adt(adt,cap,p,13000000);
}
''')
        lib=d/'contract.so';subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-shared','-fPIC','-DHANDOFF_HOST_TEST','-I',str(shim),'-I',str(headers),str(d/'contract.c'),str(shim/'handoff.c'),str(shim/'adt.c'),'-o',str(lib)],check=True)
        cls.lib=ctypes.CDLL(str(lib));cls.lib.parse.argtypes=[ctypes.c_void_p,ctypes.c_uint]
        cls.lib.contract.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t];cls.lib.contract.restype=ctypes.c_size_t

    def contract(self,b):
        adt=ctypes.create_string_buffer(PAGE);n=self.lib.contract(bytes(b),len(b),adt,PAGE);return n,adt.raw[:n]
    def packed(self,fw=None):
        return build(kernel_fixture(),cmdline='rd=emmcroot0',gpu_firmware=gpu_fixture(),wmt_firmware=self.wmt,wlan_firmware=firmware() if fw is None else fw)
    def test_v6_all_three_actual_assets_and_adt(self):
        b,info=self.packed();self.assertEqual(HEADER.unpack_from(b)[1:3],(6,4))
        assets=[SEGMENT.unpack_from(b,HEADER.size+SEGMENT.size*i) for i in (1,2,3)]
        for d,raw in zip(assets,(firmware(),self.wmt,gpu_fixture())):
            self.assertEqual(b[d[2]:d[2]+d[3]],bytes(raw));self.assertEqual(d[1],(len(raw)+PAGE-1)//PAGE*PAGE)
        self.assertEqual(assets[0][0]+assets[0][1],assets[1][0]);self.assertEqual(assets[1][0]+assets[1][1],assets[2][0])
        n,data=self.contract(b);self.assertGreater(n,0);t,end=tree(data);self.assertEqual(end,n)
        chosen=next(c for c in t[1] if c[0][b'name']==b'chosen\0');props=next(c[0] for c in chosen[1] if c[0][b'name']==b'memory-map\0')
        for key,d in zip((b'WLANFirmware-A04BR3',b'WMTFirmware-A04BR3',b'GPUFirmware-A04BR3'),assets):self.assertEqual(struct.unpack('<2Q',props[key]),(d[0],d[3]))
        self.assertNotIn(b'RAMDisk',props);self.assertFalse(info['wlan_firmware']['firmware_running'])
        armio=next(c for c in t[1] if c[0][b'name']==b'arm-io\0')
        wlan=next(c for c in armio[1] if c[0][b'name']==b'wlan\0')
        self.assertFalse(wlan[1]);w=wlan[0]
        self.assertEqual(w[b'compatible'],b'mediatek,mt6630-wlan\0')
        self.assertEqual(w[b'transport'],b'sdio\0')
        self.assertEqual(struct.unpack('<I',w[b'sdio-function']),(1,))
        self.assertEqual(struct.unpack('<Q',w[b'sdio-host-address']),(0x11170000,))
        self.assertNotIn(b'local-mac-address',w)
        self.assertNotIn(b'reg',w) # SDIO function, not a fabricated MMIO aperture

    def test_kernel_parser_matches_python_rejections(self):
        good=firmware();self.assertEqual(self.lib.parse(good,len(good)),0)
        cases=[good[:n] for n in range(len(good))]
        for at,val in [(0,0),(8,17),(12,1),(16,0),(20,1),(24,0),(28,0xfffffff0),(32,48),(44,0x9001)]:
            b=bytearray(good);struct.pack_into('<I',b,at,val);struct.pack_into('<I',b,4,zlib.crc32(b[8:]));cases.append(bytes(b))
        bad=bytearray(good);bad[-1]^=1;cases.append(bytes(bad))
        for b in cases:
            self.assertNotEqual(self.lib.parse(b,len(b)),0)
            with self.assertRaises(ValueError):wlan_firmware.stage(b)
    def test_descriptor_entry_alias_root_and_trailer_rejected(self):
        b,info=self.packed();h=HEADER.unpack_from(b);w=SEGMENT.unpack_from(b,720)
        cases=[(12,'<I',3),(48,'<Q',info['kernel_end_pa']-PAGE),(720,'<Q',w[0]+PAGE),(728,'<Q',w[1]+PAGE),(736,'<Q',w[2]+8),(744,'<Q',31),(40,'<Q',h[3]+w[0]-h[4])]
        for at,fmt,v in cases:
            bad=bytearray(b);struct.pack_into(fmt,bad,at,v);self.assertEqual(self.contract(bad)[0],0)
        for root in (b'rd=md0',b'rd=emmcroot0 rd=emmcroot0',b'rd=emmcroot0\trd=md0'):
            bad=bytearray(b);bad[80:688]=root.ljust(608,b'\0');self.assertEqual(self.contract(bad)[0],0)
        bad=b+bytes(8);bad=bytearray(bad);struct.pack_into('<Q',bad,72,len(bad));self.assertEqual(self.contract(bad)[0],0)
        self.assertEqual(self.contract(b[:-1])[0],0)
        for args in ({'wmt_firmware':None},{'gpu_firmware':None},{'ramdisk':b'root'}):
            kw=dict(cmdline='rd=emmcroot0',wlan_firmware=firmware(),wmt_firmware=self.wmt,gpu_firmware=gpu_fixture());kw.update(args)
            with self.assertRaises(ValueError):build(kernel_fixture(),**kw)
    def test_external_matching_bsp_file(self):
        p=ROOT.parent.parent.parent/'port/gsi/vendor/benesse/a04br3/proprietary/vendor/firmware/WIFI_RAM_CODE_MT6630'
        if not p.is_file():self.skipTest('external BSP firmware not supplied')
        raw,meta=wlan_firmware.load(p);self.assertEqual(len(meta['regions']),2);self.assertEqual(self.lib.parse(raw,len(raw)),0)
        b,info=self.packed(raw);self.assertGreater(self.contract(b)[0],0);self.assertEqual(info['wlan_firmware']['sha256'],meta['source_sha256'])

if __name__=='__main__':unittest.main()
