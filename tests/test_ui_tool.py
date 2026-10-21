# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual CLI and public C ABI mocks; no device/SDK execution."""
from pathlib import Path
import subprocess
import tempfile
import unittest
TOOLS=Path(__file__).resolve().parents[1]/'tools'
PUBLIC='''#pragma once
#include <stdint.h>
typedef uint32_t mach_port_t;typedef int kern_return_t;
typedef const void *CFDictionaryRef;typedef void *CFMutableDictionaryRef;
#define MACH_PORT_NULL 0
#define KERN_SUCCESS 0
extern mach_port_t mach_task_self(void);
'''
CODE=r'''
#include <assert.h>
#include <stdlib.h>
#include <sys/types.h>
#include <unistd.h>
static uid_t ui_euid(void);
#define geteuid ui_euid
#define main ui_main
#include "a04-ui-test.c"
#undef main
#undef geteuid
static unsigned mode,services,connections,calls,matching,kind;static uint64_t x,y;
static uid_t ui_euid(void){return mode==1?501:0;}
mach_port_t mach_task_self(void){return 7;}
CFMutableDictionaryRef IOServiceMatching(const char *name){assert(!strcmp(name,"A04BR3HIDButtons"));++matching;return mode==2?NULL:(void*)1;}
mach_port_t IOServiceGetMatchingService(mach_port_t master,CFDictionaryRef d){assert(!master&&d==(void*)1);if(mode==3)return 0;++services;return 11;}
kern_return_t IOServiceOpen(mach_port_t service,mach_port_t task,uint32_t type,mach_port_t *c){assert(service==11&&task==7&&type==A04_UI_CLIENT_TYPE);if(mode==4)return 1;if(mode==5)return 0;++connections;*c=22;return mode==6?1:0;}
kern_return_t IOObjectRelease(mach_port_t service){assert(service==11&&services==1);--services;return 0;}
kern_return_t IOServiceClose(mach_port_t c){assert(c==22&&connections==1);--connections;return mode==9?1:0;}
kern_return_t IOConnectCallScalarMethod(mach_port_t c,uint32_t selector,const uint64_t *s,uint32_t n,uint64_t *out,uint32_t *count){assert(c==22&&!services&&selector==A04_UI_SELECTOR&&n==3&&!out&&!*count);assert(s[0]==kind&&s[1]==x&&s[2]==y);++calls;if(mode==8)*count=1;return mode==7?1:0;}
static void check(int argc,const char **argv,int expected){unsigned before=matching;int r=ui_main(argc,(char**)argv);assert(r==expected&&!services&&!connections);if(expected==2||mode==1)assert(before==matching);}
int main(){
 const char *home[]={"a04-ui-test","home"};kind=A04_UI_HOME;
 for(mode=0;mode<10;mode++){calls=0;check(2,home,mode?1:0);assert(calls==(mode==0||mode>=7?1u:0u));}mode=0;
 const char *none[]={"a04-ui-test"};check(1,none,2);
 const char *badops[]={"power","reset","down","up","Home","home ","--help","tap",""};
 for(unsigned i=0;i<sizeof(badops)/sizeof(*badops);i++){const char *a[]={"a04-ui-test",badops[i]};check(2,a,2);}
 const char *invalid[]={"-1","+0",""," 1","1 ","1x","0x10","1e2","18446744073709551615","999999999999999999999999999999999999999","800","1280"};
 for(unsigned i=0;i<sizeof(invalid)/sizeof(*invalid);i++){const char *a[]={"a04-ui-test","tap",invalid[i],"0"};check(4,a,2);a[2]="0";a[3]=invalid[i];if(i!=10)check(4,a,2);}
 kind=A04_UI_TAP;const char *tap[]={"a04-ui-test","tap","0","0"};x=y=0;check(4,tap,0);tap[2]="799";tap[3]="1279";x=799;y=1279;check(4,tap,0);tap[2]="000400";tap[3]="000640";x=400;y=640;check(4,tap,0);
 const char *extra[]={"a04-ui-test","home","0"};check(3,extra,2);
 puts("PASS actual UI tool CLI/root/Mach ownership/error paths");
}
'''
class UITool(unittest.TestCase):
    def test_actual_cli_abi_cleanup_and_arm64(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'public.h').write_text(PUBLIC)
            for name in ['CoreFoundation/CoreFoundation.h','mach/mach.h']:
                f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#include "public.h"\n')
            (p/'test.c').write_text(CODE)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-no-pie','-I',str(p),'-I',str(TOOLS),str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True,capture_output=True)
            # ABI declarations compile as ARM64 with ONLY CF/mach and libc stubs.
            (p/'stdio.h').write_text('#include <stddef.h>\ntypedef struct FILE FILE;extern FILE *stderr;extern int fprintf(FILE*,const char*,...);extern int printf(const char*,...);\n')
            (p/'string.h').write_text('extern int strcmp(const char*,const char*);\n')
            (p/'unistd.h').write_text('extern unsigned geteuid(void);\n')
            subprocess.run(['clang','--target=arm64-apple-ios16.0','-std=c11','-ffreestanding','-Wall','-Wextra','-Werror','-I',str(p),'-c',str(TOOLS/'a04-ui-test.c'),'-o',str(p/'tool.o')],check=True)
            subprocess.run(['bash','-n',str(TOOLS/'build-a04-ui-test.sh')],check=True)
    def test_kernel_constants_match(self):
        import re
        root=TOOLS.parents[1]/'kernel/iokit/Kernel/arm/hid/A04BR3UITest.h'
        for key in ['A04_UI_CLIENT_TYPE','A04_UI_SELECTOR','A04_UI_HOME','A04_UI_TAP']:
            definition=lambda p:re.search(r'^#define '+key+r' (\S+)',p.read_text(),re.M).group(1)
            self.assertEqual(definition(root),definition(TOOLS/'a04-ui-test.c'))
if __name__=='__main__':unittest.main()
