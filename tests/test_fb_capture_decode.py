# SPDX-License-Identifier: BSD-2-Clause-Patent
import base64
import hashlib
import importlib.util
from pathlib import Path
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
TOOLS=Path(__file__).resolve().parents[1]/'tools'
spec=importlib.util.spec_from_file_location('a04_fb_decode',TOOLS/'a04-fb-decode.py')
decoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(decoder)
ID='0123456789abcdef0123456789abcdef'
OTHER='fedcba9876543210fedcba9876543210'
PPM=decoder.HEADER+bytes((n*73+19)&255 for n in range(200*320*3))

def records(ppm=PPM,frame_id=ID):
    prefix=f'!A04FB1 {frame_id} '
    result=[f'{prefix}BEGIN 200 320 P6 {len(ppm)}\n'.encode()]
    for offset in range(0,len(ppm),384):
        block=ppm[offset:offset+384]
        result.append(f'{prefix}DATA {offset} {len(block)} {zlib.crc32(block)&0xffffffff:08x} '.encode()+base64.b64encode(block)+b'\n')
    result.append(f'{prefix}END {len(ppm)} {hashlib.sha256(ppm).hexdigest()}\n'.encode())
    return result

class FBCaptureDecode(unittest.TestCase):
    def test_noise_ids_chunk_boundaries_crlf_and_png_exact_pixels(self):
        selected=records();other=records(frame_id=OTHER)
        data=b'native boot log\r\n@A04 echo exec a04-fb-capture\n'+other[0]+b'x'*5000+b'\n'
        for i,line in enumerate(selected):
            data+=line.replace(b'\n',b'\r\r\n')+b'kernel: not a capture\n'
            if i<len(other):data+=other[i]
        for step in (1,7,1023,65536):
            self.assertEqual(decoder.decode_chunks((data[n:n+step] for n in range(0,len(data),step)),ID),PPM)
        png=decoder.png_from_ppm(PPM);self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        at=8;compressed=b'';types=[]
        while at<len(png):
            size=struct.unpack('>I',png[at:at+4])[0];kind=png[at+4:at+8];body=png[at+8:at+8+size]
            self.assertEqual(struct.unpack('>I',png[at+8+size:at+12+size])[0],zlib.crc32(kind+body)&0xffffffff)
            types.append(kind)
            if kind==b'IHDR':self.assertEqual(struct.unpack('>IIBBBBB',body),(200,320,8,2,0,0,0))
            if kind==b'IDAT':compressed+=body
            at+=12+size
        raw=zlib.decompress(compressed)
        self.assertEqual(len(raw),320*601);self.assertEqual(types,[b'IHDR',b'IDAT',b'IEND'])
        self.assertTrue(all(raw[y*601]==0 for y in range(320)))
        self.assertEqual(b''.join(raw[y*601+1:(y+1)*601] for y in range(320)),PPM[len(decoder.HEADER):])

    def test_incomplete_replay_corruption_bounds_and_no_partial_accept(self):
        good=records()
        bad=[]
        bad += [good[1:],good[:-1],good[:3]+good[4:],good[:3]+[good[2]]+good[3:],good[:2]+[good[0]]+good[2:],good+[good[-1]]]
        bad += [[good[0].replace(b'200 320',b'201 320')]+good[1:]]
        bad += [[good[0]]+[good[1].replace(b'DATA 0 384',b'DATA 1 384')]+good[2:]]
        fields=good[1].split();fields[-2]=b'00000000';bad += [[good[0],b' '.join(fields)+b'\n']+good[2:]]
        fields=good[1].split();fields[-1]=b'!'+fields[-1][1:];bad += [[good[0],b' '.join(fields)+b'\n']+good[2:]]
        bad += [good[:-1]+[good[-1].split(b' END ')[0]+b' END 192015 '+b'0'*64+b'\n']]
        bad += [good[:-1]+[good[-1].replace(b'END 192015',b'END 999999')]]
        bad += [[good[0],f'!A04FB1 {ID} DATA '.encode()+b'x'*2000+b'\n']+good[2:]]
        bad += [good[:-1]+[good[-1][:-1]]]
        bad += [records(ppm=b'X'+PPM[1:])]
        for case in bad:
            with self.assertRaises(decoder.CaptureError):decoder.decode_chunks(case,ID)
        with self.assertRaises(decoder.CaptureError):decoder.decode_chunks(records(frame_id=OTHER),ID)
        for frame_id in ('',ID.upper(),'../bad',ID+'0'):
            with self.assertRaises(decoder.CaptureError):decoder.decode_chunks(good,frame_id)

    def test_regular_log_baseline_cli_png_and_safe_failure(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);log=p/'collector.log';old=b''.join(records());body=b''.join(records())
            log.write_bytes(old+body)
            self.assertEqual(decoder.decode_log(log,ID,len(old)),PPM)
            with self.assertRaises(decoder.CaptureError):decoder.decode_log(log,ID) # Reused ID across two captures.
            for offset in (-1,len(old+body)+1):
                with self.assertRaises(decoder.CaptureError):decoder.decode_log(log,ID,offset)
            fifo=p/'not-log';os.mkfifo(fifo)
            with self.assertRaises(decoder.CaptureError):decoder.decode_log(fifo,ID)
            out=p/'screen.png';cmd=[sys.executable,str(TOOLS/'a04-fb-decode.py'),'--log',str(log),'--frame-id',ID,'--start-offset',str(len(old)),'--output',str(out)]
            result=subprocess.run(cmd,capture_output=True);self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(out.read_bytes(),decoder.png_from_ppm(PPM))
            result=subprocess.run(cmd,capture_output=True);self.assertNotEqual(result.returncode,0)
            self.assertEqual(out.read_bytes(),decoder.png_from_ppm(PPM))
            out.unlink();log.write_bytes(body[:-50])
            result=subprocess.run(cmd[:cmd.index('--start-offset')]+['--output',str(out)],capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertFalse(out.exists())

    def test_sdk_script_quoted_paths_frameworks_and_separate_signing(self):
        # Exercise the build recipe with a mocked SDK toolchain, NOT an SDK link.
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);bin_dir=p/'bin';bin_dir.mkdir();record=p/'calls.jsonl'
            (bin_dir/'xcrun').write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]
if args==['--sdk','iphoneos','--show-sdk-path']:print(os.environ['CAPTURE_TEST_SDK']);sys.exit(0)
with open(os.environ['CAPTURE_TEST_RECORD'],'a') as f:f.write(json.dumps(args)+'\\n')
pathlib.Path(args[args.index('-o')+1]).write_bytes(b'MOCK SDK LINK ONLY')
""")
            (bin_dir/'codesign').write_text("""#!/usr/bin/env python3
import json,os,sys
with open(os.environ['CAPTURE_TEST_RECORD'],'a') as f:f.write(json.dumps(['codesign']+sys.argv[1:])+'\\n')
""")
            for tool in bin_dir.iterdir():tool.chmod(0o755)
            env=dict(os.environ,PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],CAPTURE_TEST_SDK=str(p/'sdk with spaces'),CAPTURE_TEST_RECORD=str(record))
            out=p/'out with spaces'/'capture'
            subprocess.run(['bash',str(TOOLS/'build-a04-fb-capture.sh'),str(out)],env=env,check=True)
            import json
            calls=[json.loads(line) for line in record.read_text().splitlines()]
            cc=calls[0];self.assertEqual(cc[:3],['--sdk','iphoneos','clang'])
            self.assertEqual(cc[cc.index('-isysroot')+1],env['CAPTURE_TEST_SDK'])
            self.assertEqual(cc[cc.index('-target')+1],'arm64-apple-ios16.0')
            self.assertEqual([cc[i+1] for i,v in enumerate(cc) if v=='-framework'],['CoreFoundation','IOKit','IOSurface'])
            self.assertEqual(calls[1],['codesign','--force','--sign','-','--timestamp=none',str(out)])
            manifest=Path(str(out)+'.sha256').read_text()
            for name in ('a04-fb-capture.c','a04-fb-capture.h','a04-fb-capture-digest.h','build-a04-fb-capture.sh'):self.assertIn(name,manifest)
            self.assertIn(hashlib.sha256(out.read_bytes()).hexdigest(),manifest)

    def test_portable_digest_known_boundary_vectors(self):
        source=r'''
#include <stdio.h>
#include "a04-fb-capture-digest.h"
int main(void){unsigned char input[131],digest[32];for(unsigned i=0;i<131;i++)input[i]=(unsigned char)(i*73+19);
 for(unsigned n=0;n<=131;n++){
  a04_fb_sha256(input,n,digest);printf("%u %08x ",n,a04_fb_crc32(input,n));
  for(unsigned i=0;i<32;i++)printf("%02x",digest[i]);printf("\n");
 }
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'digest.c').write_text(source)
            subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(TOOLS),str(p/'digest.c'),'-o',str(p/'digest')],check=True)
            result=subprocess.run([str(p/'digest')],capture_output=True,check=True)
            data=bytes((n*73+19)&255 for n in range(131))
            for line in result.stdout.decode().splitlines():
                n,crc,sha=line.split();block=data[:int(n)]
                self.assertEqual(crc,f'{zlib.crc32(block)&0xffffffff:08x}');self.assertEqual(sha,hashlib.sha256(block).hexdigest())

if __name__=='__main__':unittest.main()
