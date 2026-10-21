# SPDX-License-Identifier: BSD-2-Clause-Patent
from pathlib import Path
import os
import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
TOOLS=Path(__file__).resolve().parents[1]/'tools'
spec=importlib.util.spec_from_file_location('a04_fb_decode',TOOLS/'a04-fb-decode.py')
decoder=importlib.util.module_from_spec(spec);spec.loader.exec_module(decoder)

PUBLIC_STUB=r'''
#pragma once
#include <stddef.h>
#include <stdint.h>
typedef uint32_t mach_port_t;typedef int kern_return_t;
#define MACH_PORT_NULL 0
#define KERN_SUCCESS 0
extern mach_port_t mach_task_self(void);
extern kern_return_t mach_port_deallocate(mach_port_t,mach_port_t);
typedef const void *CFDictionaryRef;typedef void *CFMutableDictionaryRef;
extern void CFRelease(const void *);
typedef struct FakeSurface *IOSurfaceRef;
enum {kIOSurfaceLockReadOnly=1};
extern IOSurfaceRef IOSurfaceLookupFromMachPort(mach_port_t);
extern size_t IOSurfaceGetWidth(IOSurfaceRef),IOSurfaceGetHeight(IOSurfaceRef),IOSurfaceGetBytesPerRow(IOSurfaceRef),IOSurfaceGetBytesPerElement(IOSurfaceRef),IOSurfaceGetAllocSize(IOSurfaceRef);
extern uint32_t IOSurfaceGetPixelFormat(IOSurfaceRef);
extern void *IOSurfaceGetBaseAddress(IOSurfaceRef);
extern kern_return_t IOSurfaceLock(IOSurfaceRef,uint32_t,uint32_t *),IOSurfaceUnlock(IOSurfaceRef,uint32_t,uint32_t *);
'''

def public_stubs(p):
    (p/'public.h').write_text(PUBLIC_STUB)
    for name in ['CoreFoundation/CoreFoundation.h','IOSurface/IOSurface.h','mach/mach.h']:
        f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#include "public.h"\n')

class FBCapture(unittest.TestCase):
    def test_actual_tool_readonly_owned_snapshot_layout_failures_and_file_safety(self):
        code=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
static ssize_t capture_write(int,const void *,size_t);
static int capture_close(int);
static void *capture_malloc(size_t);
static void capture_free(void *);
#define write capture_write
#define close capture_close
#define malloc capture_malloc
#define free capture_free
#define main capture_main
#include "a04-fb-capture.c"
#undef main
#undef write
#undef close
#undef malloc
#undef free
static unsigned mode,locks,unlocks,releases,connections,services,ports,allocations,writes,delays;
static unsigned char *frame;
struct FakeSurface {int unused;};static struct FakeSurface surface;
static void *capture_malloc(size_t n){assert(n==A04_FB_BYTES || n==256000 || n==192015);if(mode==23 || (mode==30 && n==192015))return NULL;++allocations;return malloc(n);}
static void capture_free(void *p){if(p){assert(allocations);--allocations;free(p);}}
static ssize_t capture_write(int fd,const void *p,size_t n){
 assert(!connections && !services && !ports && !locks);++writes;
 if(mode==20 || (mode==28 && writes==4)){errno=ENOSPC;return -1;}
 if(mode==21 || mode==27){if(writes==1){errno=EINTR;return -1;}if(n>101)n=101;}
 return write(fd,p,n);
}
static int capture_close(int fd){int r=close(fd);if(mode==22){errno=EIO;return -1;}return r;}
mach_port_t mach_task_self(void){return 5;}
kern_return_t mach_port_deallocate(mach_port_t task,mach_port_t port){assert(task==5 && port==33 && ports==1);--ports;return 0;}
CFMutableDictionaryRef IOServiceMatching(const char *name){assert(!strcmp(name,"AppleCLCD"));return mode==1?NULL:(void*)1;}
mach_port_t IOServiceGetMatchingService(mach_port_t master,CFDictionaryRef d){assert(master==0 && d==(void*)1);if(mode==2)return 0;++services;return 11;}
kern_return_t IOServiceOpen(mach_port_t s,mach_port_t task,uint32_t type,mach_port_t *c){assert(s==11 && task==5 && !type);if(mode==3)return 1;++connections;*c=22;return 0;}
kern_return_t IOServiceClose(mach_port_t c){assert(c==22 && connections==1);--connections;return 0;}
kern_return_t IOObjectRelease(mach_port_t s){assert(s==11 && services==1);--services;return 0;}
kern_return_t IOConnectCallScalarMethod(mach_port_t c,uint32_t selector,const uint64_t *in,uint32_t inc,uint64_t *out,uint32_t *count){
 assert(c==22 && !in && !inc);
 if(selector==8){assert(*count==2);out[0]=mode==4?801:800;out[1]=1280;if(mode==24)*count=1;return 0;}
 assert(selector==3 && *count==1);
 *out=mode==5?0:(mode==6?UINT64_C(0x100000001):33);if(mode==25){*count=0;return 1;}
 if(mode!=5 && mode!=6)++ports;return 0;
}
IOSurfaceRef IOSurfaceLookupFromMachPort(mach_port_t p){assert(p==33);return mode==7?NULL:&surface;}
size_t IOSurfaceGetWidth(IOSurfaceRef s){assert(s==&surface);return mode==8?799:800;}
size_t IOSurfaceGetHeight(IOSurfaceRef s){assert(s==&surface);return mode==9?1279:1280;}
size_t IOSurfaceGetBytesPerRow(IOSurfaceRef s){assert(s==&surface);return mode==10?3199:3200;}
size_t IOSurfaceGetBytesPerElement(IOSurfaceRef s){assert(s==&surface);return mode==12?3:4;}
size_t IOSurfaceGetAllocSize(IOSurfaceRef s){assert(s==&surface);return mode==13?A04_FB_BYTES-1:A04_FB_BYTES;}
uint32_t IOSurfaceGetPixelFormat(IOSurfaceRef s){assert(s==&surface);return mode==11?0x52474241:A04_FB_BGRA;}
void *IOSurfaceGetBaseAddress(IOSurfaceRef s){assert(s==&surface && locks);return mode==15?NULL:(mode==32?frame+1:(mode==16?(void*)(UINTPTR_MAX-31):frame));}
kern_return_t IOSurfaceLock(IOSurfaceRef s,uint32_t flags,uint32_t *seed){assert(s==&surface && flags==1 && !locks);if(mode==14)return 1;*seed=73;++locks;return 0;}
kern_return_t IOSurfaceUnlock(IOSurfaceRef s,uint32_t flags,uint32_t *seed){assert(s==&surface && flags==1 && *seed==73 && locks==1);--locks;++unlocks;return mode==17?1:0;}
void CFRelease(const void *s){assert(s==&surface && !locks);++releases;
 assert(!mprotect(frame,A04_FB_BYTES,PROT_READ|PROT_WRITE));memset(frame,0,A04_FB_BYTES); // Original backing changes before slow output.
}
int main(int argc,char **argv){
 assert(argc==3);mode=(unsigned)strtoul(argv[1],NULL,10);
 frame=mmap(NULL,A04_FB_BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(frame!=MAP_FAILED);
 for(unsigned y=0;y<1280;y++)for(unsigned x=0;x<800;x++){
  unsigned char *p=frame+y*3200+x*4;p[0]=(unsigned char)(x*3);p[1]=(unsigned char)(y>>2);p[2]=(unsigned char)(x^y);p[3]=0x7d;
 }
 assert(!mprotect(frame,A04_FB_BYTES,PROT_READ)); // Any source write by tool faults.
 char *args[]={"a04-fb-capture",mode==18?"relative.ppm":argv[2],NULL};
 char *preview_args[]={"a04-fb-capture","--uart-preview",mode==29?"bad":(mode==31?"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA":"0123456789abcdef0123456789abcdef"),NULL};
 int result=mode>=26?capture_main(3,preview_args):capture_main(2,args);
 assert(result==((mode==0 || mode==21 || mode==26 || mode==27)?0:((mode==18 || mode==29 || mode==31)?2:1)));
 assert(!locks && !connections && !services && !ports && !allocations && !delays);
 if(mode==0 || mode==21 || mode==26 || mode==27)assert(releases==1 && unlocks==1 && writes>1);
 assert(!munmap(frame,A04_FB_BYTES));return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);public_stubs(p);(p/'test.c').write_text(code)
            subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            '-I',str(p),'-I',str(TOOLS),str(p/'test.c'),'-o',str(p/'test')],check=True)
            for mode in range(33):
                output=p/f'capture-{mode}.ppm'
                if mode==19:output.write_bytes(b'keep-existing')
                result=subprocess.run([str(p/'test'),str(mode),str(output)],capture_output=True)
                self.assertEqual(result.returncode,0,result.stderr.decode())
                if mode in (26,27):
                    ppm=decoder.decode_chunks([result.stdout], '0123456789abcdef0123456789abcdef')
                    expected=bytes(v for y in range(0,1280,4) for x in range(0,800,4) for v in ((x^y)&255,(y>>2)&255,(x*3)&255))
                    self.assertEqual(ppm,decoder.HEADER+expected)
                    self.assertFalse(output.exists())
                    self.assertLessEqual(max(map(len,result.stdout.splitlines())),640)
                elif mode==28:
                    with self.assertRaises(decoder.CaptureError):
                        decoder.decode_chunks([result.stdout],'0123456789abcdef0123456789abcdef')
                    self.assertFalse(output.exists())
                elif mode in (0,21):
                    raw=output.read_bytes();header=b'P6\n800 1280\n255\n'
                    self.assertEqual(raw[:len(header)],header)
                    self.assertEqual(len(raw),len(header)+800*1280*3)
                    expected=bytes(v for y in range(1280) for x in range(800) for v in ((x^y)&255,(y>>2)&255,(x*3)&255))
                    self.assertEqual(raw[len(header):],expected)
                elif mode==19:self.assertEqual(output.read_bytes(),b'keep-existing')
                else:self.assertFalse(output.exists(),f'mode{mode} left partial output')

    def test_bounded_conversion_and_original_arm64_target_compile(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);public_stubs(p)
            (p/'pure.c').write_text(r'''
#include <assert.h>
#include <string.h>
#include "a04-fb-capture.h"
int main(void){unsigned char src[3200],dst[2402];memset(src,0,3200);memset(dst,0xa5,2402);
 src[0]=17;src[1]=34;src[2]=51;src[3]=68;size_t bytes=0;
 assert(!a04_fb_layout(800,1280,3200,A04_FB_BGRA,4,4096000,&bytes) && bytes==4096000);
 assert(!a04_fb_rgb_row(dst+1,2400,src,3200) && dst[1]==51 && dst[2]==34 && dst[3]==17 && dst[0]==0xa5 && dst[2401]==0xa5);
 memset(dst,0xa5,sizeof(dst));assert(a04_fb_rgb_row(dst+1,2399,src,3200));
 assert(a04_fb_rgb_row(dst+1,2400,src,3199));assert(a04_fb_rgb_row(src,3200,src,3200));
 for(unsigned i=0;i<sizeof(dst);i++)assert(dst[i]==0xa5);
 assert(a04_fb_rgb_row(NULL,2400,src,3200));assert(a04_fb_layout(800,1280,3200,A04_FB_BGRA,4,4095999,&bytes));
 assert(a04_fb_layout(800,1280,3200,A04_FB_BGRA,4,4096000,NULL));}
''')
            subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            '-I',str(TOOLS),str(p/'pure.c'),'-o',str(p/'pure')],check=True)
            subprocess.run([str(p/'pure')],check=True)
            # Public-ABI stubs only; retain complete unmodified target source.
            # This target compile is not an actual Apple SDK link.
            (p/'errno.h').write_text('extern int *__error(void);\n#define errno (*__error())\n#define EINTR 4\n')
            (p/'fcntl.h').write_text('#define O_WRONLY 1\n#define O_CREAT 0x200\n#define O_EXCL 0x800\nextern int open(const char*,int,...);\n')
            (p/'stdio.h').write_text('#include <stddef.h>\ntypedef struct StubFILE FILE;extern FILE *stderr;extern int fprintf(FILE*,const char*,...);extern int snprintf(char*,size_t,const char*,...);\n')
            (p/'stdlib.h').write_text('#include <stddef.h>\nextern void *malloc(size_t);extern void free(void*);\n')
            (p/'string.h').write_text('#include <stddef.h>\nextern void *memcpy(void*,const void*,size_t);extern size_t strlen(const char*);extern int strcmp(const char*,const char*);\n')
            (p/'unistd.h').write_text('#include <stddef.h>\ntypedef long ssize_t;extern ssize_t write(int,const void*,size_t);extern int close(int),unlink(const char*);\n')
            subprocess.run(['clang','--target=arm64-apple-ios16.0','-std=c11','-ffreestanding','-O2',
                            '-Wall','-Wextra','-Werror','-Wframe-larger-than=4096','-I',str(p),'-I',str(TOOLS),
                            '-c',str(TOOLS/'a04-fb-capture.c'),'-o',str(p/'capture.o')],check=True)
            subprocess.run(['bash','-n',str(TOOLS/'build-a04-fb-capture.sh')],check=True)

if __name__=='__main__':unittest.main()
