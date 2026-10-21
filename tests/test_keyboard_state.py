# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Standalone source-reader validation; no donor image, SDK or live PID needed."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'tools/a04br3_keyboard_state.c'

class KeyboardStateTest(unittest.TestCase):
    def test_read_only_snapshot_rejects_wrong_image_and_bad_reads(self):
        harness = r'''
#define A04_KBD_STATE_TEST 1
#include "a04br3_keyboard_state.c"
#include <assert.h>
static unsigned char image[256], globals[65536], app[256], impl[2048], remote[256];
static unsigned reads, globals_read, cached_reads;
static int fail, short_read, once_fail;
int mach_vm_read_overwrite(unsigned task,uint64_t a,uint64_t n,uint64_t dst,uint64_t *got) {
 (void)task;++reads;if(a==0x1e1db8cf8)++cached_reads;
 if(fail||(once_fail&&a==0x1e1db8d00))return 5;
 struct region {uint64_t base;unsigned char *data;size_t size;};
 struct region regions[]={{0x188d83000,image,sizeof(image)},{0x1e1db0000,globals,sizeof(globals)},
 {0x200000000,app,sizeof(app)},{0x200001000,impl,sizeof(impl)},{0x200002000,remote,sizeof(remote)}};
 for(unsigned i=0;i<sizeof(regions)/sizeof(regions[0]);i++){
  struct region *r=&regions[i];if(a>=r->base&&a-r->base<=r->size&&n<=r->size-(a-r->base)){
   if(i==1)++globals_read;memcpy((void*)(uintptr_t)dst,r->data+a-r->base,(size_t)n);*got=short_read?n-1:n;return 0;
  }
 }return 1;
}
static void word(unsigned char *p,uint64_t value,unsigned bytes){memcpy(p,&value,bytes);}
static void reset(void){
 memset(image,0,sizeof(image));memset(globals,0,sizeof(globals));memset(app,0,sizeof(app));
 memset(impl,0,sizeof(impl));memset(remote,0,sizeof(remote));reads=globals_read=cached_reads=keyboard_reads=0;
 fail=short_read=once_fail=0;keyboard_slide=0;
 word(image,0xfeedfacf,4);word(image+4,0x100000c,4);word(image+16,1,4);word(image+20,24,4);
 word(image+32,0x1b,4);word(image+36,24,4);memcpy(image+40,keyboard_uuid,16);
 word(globals+0x31c0,0x200000000,8);word(globals+0x8d90,0x200001000,8);
 word(globals+0x8d00,UINT64_MAX,8);word(globals+0x8cf8,1,1);
 word(globals+0x3e30,0x200002000,8);word(globals+0x3e38,UINT64_MAX,8);
 word(app+0xbc,1,1);word(remote+0x38,1,1);word(remote+0xd0,0x200003000,8);
}
int main(void){
 unsigned cases=0;uint64_t v=0;unsigned char scratch[65];
 reset();assert(keyboard_snapshot()==0&&globals_read>0&&keyboard_reads<=128&&cached_reads==1);++cases;
 reset();image[40]^=1;assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image,0,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+4,0,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+16,97,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+36,0,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+36,25,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+36,0xfffffff8,4);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(image+16,2,4);word(image+20,48,4);memcpy(image+56,image+32,24);assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();fail=1;assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();short_read=1;assert(keyboard_snapshot()==2&&globals_read==0);++cases;
 reset();word(globals+0x3e30,0,8);assert(keyboard_snapshot()==0);++cases;
 reset();assert(keyboard_read(0,scratch,1)!=0&&reads==0);++cases;
 reset();assert(keyboard_read(UINT64_MAX,scratch,1)!=0&&reads==0);++cases;
 reset();assert(keyboard_read(0x188d83000,scratch,65)!=0&&reads==0);++cases;
 reset();keyboard_reads=128;assert(keyboard_read(0x188d83000,scratch,1)!=0&&reads==0);++cases;
 reset();assert(keyboard_value("checked-byte",0x2000000bc,1,&v)==0&&v==1);++cases;
 reset();word(globals+0x8d00,0,8);assert(keyboard_snapshot()==0&&cached_reads==0);++cases;
 reset();fail=1;v=123;assert(keyboard_value("failed-value",0x2000000bc,1,&v)!=0&&v==123);++cases;
 reset();once_fail=1;assert(keyboard_snapshot()==0&&cached_reads==0);++cases;
 reset();word(globals+0x8d00,1,8);assert(keyboard_snapshot()==0&&cached_reads==0);++cases;
 printf("PASS %u passive-reader policy/fault cases; no live UIKit execution\n",cases);
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / SOURCE.name).write_bytes(SOURCE.read_bytes())
            (root / 'harness.c').write_text(harness)
            executable = root / 'probe-test'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-misleading-indentation', '-fsanitize=address,undefined', '-no-pie',
                str(root / 'harness.c'), '-o', str(executable)], check=True, capture_output=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn('PASS 21 passive-reader', result.stdout)
            self.assertIn('remoteExpectingInitialState=0x1', result.stdout)
            self.assertIn('remoteConnection=0x200003000', result.stdout)
            self.assertIn('remoteConnection unavailable-object', result.stdout)
            self.assertIn('inputUIOOPCached=0x1', result.stdout)
            self.assertIn('inputUIOOPCached unavailable-uninitialized-or-unreadable-once', result.stdout)
            self.assertNotIn('failed-value=0x0', result.stdout)

    def test_actual_cli_root_arguments_task_cleanup_and_arm64(self):
        declarations = """#pragma once
#include <stdint.h>
typedef unsigned mach_port_t;
extern mach_port_t mach_task_self(void);
extern int task_for_pid(mach_port_t,int,mach_port_t*);
extern int mach_port_deallocate(mach_port_t,mach_port_t);
extern int mach_vm_read_overwrite(mach_port_t,uint64_t,uint64_t,uint64_t,uint64_t*);
"""
        harness = r'''
#include <assert.h>
#include <sys/types.h>
#include <unistd.h>
static uid_t reader_euid(void);
#define geteuid reader_euid
#define main reader_main
#include "a04br3_keyboard_state.c"
#undef main
#undef geteuid
static unsigned mode, tasks, releases, reads;
static uid_t reader_euid(void){return mode==1?501:0;}
mach_port_t mach_task_self(void){return 7;}
int task_for_pid(mach_port_t self,int pid,mach_port_t *task){assert(self==7&&pid==42);++tasks;*task=9;return mode==2?5:0;}
int mach_port_deallocate(mach_port_t self,mach_port_t task){assert(self==7&&task==9);++releases;return 0;}
int mach_vm_read_overwrite(mach_port_t task,uint64_t a,uint64_t n,uint64_t dst,uint64_t *got){(void)a;(void)n;(void)dst;(void)got;assert(task==9);++reads;return 5;}
int main(void){
 char *valid[]={"reader","42","0"};mode=1;
 assert(reader_main(3,valid)==77&&!tasks&&!reads&&!releases);mode=0;
 assert(reader_main(1,valid)==64&&!tasks);
 const char *badpid[]={"", "-42", "+42", " 42", "\t42", "42 ", "1", "2147483648", "99999999999999999999999999"};
 for(unsigned i=0;i<sizeof(badpid)/sizeof(*badpid);i++){char *v[]={"reader",(char*)badpid[i],"0"};assert(reader_main(3,v)==64&&!tasks);}
 const char *badslide[]={"", "-0", "+0", " 0", "\t0", "0 ", "1", "100001000", "ffffffffffffffffffffffff"};
 for(unsigned i=0;i<sizeof(badslide)/sizeof(*badslide);i++){char *v[]={"reader","42",(char*)badslide[i]};assert(reader_main(3,v)==64&&!tasks);}
 mode=2;assert(reader_main(3,valid)==77&&tasks==1&&!reads&&!releases);
 mode=0;assert(reader_main(3,valid)==2&&tasks==2&&reads==1&&releases==1);
 puts("PASS real CLI root/parse/task denial/port release");return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p/'public.h').write_text(declarations)
            for name in ('mach/mach.h', 'mach/mach_vm.h'):
                header=p/name;header.parent.mkdir(parents=True,exist_ok=True)
                header.write_text('#include "public.h"\n')
            (p/'main.c').write_text(harness)
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                '-fsanitize=address,undefined','-no-pie','-I',str(p),'-I',str(SOURCE.parent),
                str(p/'main.c'),'-o',str(p/'main')],check=True,capture_output=True)
            subprocess.run([str(p/'main')],check=True,capture_output=True)
            # Declaration-only ARM64 code generation; not an Apple SDK/link/sign test.
            (p/'stdio.h').write_text('#include <stddef.h>\ntypedef struct FILE FILE;extern FILE *stderr;extern int fprintf(FILE*,const char*,...);extern int fputs(const char*,FILE*);extern int printf(const char*,...);extern int puts(const char*);\n')
            (p/'stdlib.h').write_text('extern unsigned long strtoul(const char*,char**,int);extern unsigned long long strtoull(const char*,char**,int);\n')
            (p/'string.h').write_text('#include <stddef.h>\nextern int memcmp(const void*,const void*,size_t);\n')
            (p/'unistd.h').write_text('extern unsigned geteuid(void);\n')
            (p/'errno.h').write_text('extern int *__error(void);\n#define errno (*__error())\n')
            subprocess.run(['clang','--target=arm64-apple-ios16.0','-std=c11','-ffreestanding',
                '-Wall','-Wextra','-Werror','-I',str(p),'-c',str(SOURCE),'-o',str(p/'reader.o')],check=True,capture_output=True)

    def test_no_mutating_mach_or_objc_operations(self):
        source = SOURCE.read_text()
        for operation in ['mach_vm_write(', 'mach_vm_protect(', 'task_suspend(',
                          'task_resume(', 'thread_create(', 'objc_msgSend(',
                          'CFPreferencesSet', 'setProperty(']:
            self.assertNotIn(operation, source)
        self.assertIn('mach_port_deallocate(', source)

if __name__ == '__main__':
    unittest.main()
