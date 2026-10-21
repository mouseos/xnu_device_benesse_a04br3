# SPDX-License-Identifier: MIT
"""Actual console + actual boot cdev, OpenSSL SHA and mocked eMMC.
UART loss/corruption is injected on host pipes. No physical device access.
"""
import ast
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
KERNEL=TOOLS.parents[1]/'kernel'
sys.path.insert(0,str(TOOLS))
import uart_client as uart

class Reconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();p=Path(cls.tmp.name);cls.exe=p/'server'
        # Reuse existing fixture construction, not its old tests. Extract its
        # literal harness expression, substitute the current actual cdev body.
        source=(KERNEL/'bsd/dev/a04br3_msdc.c').read_text()
        body=source[source.index('static LCK_MTX_DECLARE(boot_lock'):source.index('static const struct cdevsw boot_cdevsw')]
        tree=ast.parse((KERNEL/'build-support/tests/test_boot_update.py').read_text())
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='harness' for t in n.targets))
        harness=eval(compile(ast.Expression(assignment.value),'trusted-fixture','eval'),{'ROOT':KERNEL,'body':body})
        harness=harness[:harness.index('int main(void){')].replace('image[132*1024]','image[130*1024]')
        bridge=r'''
#define A04_CONSOLE_TEST 1
#define open bridge_open
#define close bridge_close
#define write bridge_write
#define ioctl bridge_ioctl
#define sync bridge_sync
#define reboot bridge_reboot
#define digest console_digest
#include "CONSOLE"
#undef open
#undef close
#undef write
#undef ioctl
#undef sync
#undef reboot
#undef digest
#include <stdarg.h>
int *__error(void){return &errno;}
static int partial,corrupt;static uint64_t file_offset;
int bridge_open(const char *name,int flags,...){assert(!strcmp(name,"/dev/a04boot")&&flags==1);int e=boot_open(0,FWRITE,0,&rootproc);if(e){errno=e;return -1;}file_offset=0;return 9;}
int bridge_close(int fd){assert(fd==9);return boot_close(0,0,0,&rootproc);}
ssize_t bridge_write(int fd,const void *data,size_t bytes){
 assert(fd==9);struct uio u={file_offset,(int64_t)bytes,data,partial};
 int e=boot_write(0,&u,0);file_offset=u.offset;
 if(corrupt && !e){((uint8_t *)boot_stage)[0]^=1;corrupt=0;}
 if(e){errno=e;return -1;}return (ssize_t)bytes;
}
int bridge_ioctl(int fd,unsigned long cmd,...){assert(fd==9);va_list a;va_start(a,cmd);char *data=va_arg(a,void*);va_end(a);
 if(cmd==BEGIN)file_offset=0;
 unsigned long kernel_cmd=cmd==INFO?A04_BOOT_INFO:cmd==BEGIN?A04_BOOT_BEGIN:cmd==COMMIT?A04_BOOT_COMMIT:cmd==ABORT?A04_BOOT_ABORT:cmd;
 int e=boot_ioctl(0,kernel_cmd,data,FWRITE,&rootproc);if(e){errno=e;return -1;}return 0;
}
void bridge_sync(void){}
int bridge_reboot(int flags){(void)flags;assert(0);return -1;}
int main(int argc,char **argv){
 assert(argc==3);assert(proc_suser(&other)==EPERM);reset();partial=!strcmp(argv[2],"partial");corrupt=!strcmp(argv[2],"corruptstage");
 FILE *f=fopen(argv[1],"wb");assert(f&&fwrite(image,1,sizeof(image),f)==sizeof(image));assert(!fclose(f));
 puts("READY");fflush(stdout);char line[2048];
 while(fgets(line,sizeof(line),stdin)){
  if(!strcmp(line,"TEST_STATE\n")){printf("STATE writes=%u flags=%u staged=%llu\n",writes,boot_flags,(unsigned long long)boot_staged);fflush(stdout);continue;}
  line[strcspn(line,"\r\n")]=0;char reply[512]={0};assert(!command(line,reply));
  if(reply[0])fputs(reply,stdout);else puts("NO_REPLY");fflush(stdout);
 }
 boot_reset();return 0;
}
'''.replace('"CONSOLE"','"'+str(TOOLS/'uart-console.c')+'"')
        (p/'server.c').write_text(harness+bridge)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-deprecated-declarations','-fsanitize=address,undefined',str(p/'server.c'),'-lcrypto','-o',str(cls.exe)],check=True)

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def scenario(self,mode,*,success=False,offset_target=0):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'boot';server_mode=mode if mode in ('partial','corruptstage') else 'normal'
            server=subprocess.Popen([str(self.exe),str(image),server_mode],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
            def line():
                result=b''
                while not result.endswith(b'\n'):
                    self.assertTrue(select.select([server.stdout],[],[],2)[0],'fixture response timeout')
                    byte=server.stdout.read(1);self.assertTrue(byte,'fixture exited');result+=byte
                return result
            self.assertEqual(line(),b'READY\n')
            pending=bytearray();sent=[];data_ids=[];fault_ids=set();now=[0.0];wire_changed=False
            def roundtrip(wire):
                server.stdin.write(wire);server.stdin.flush()
                prefix=b'STATE ' if wire==b'TEST_STATE\n' else b'!A04 '+wire.split()[1]+b' '
                reply=line()
                while not reply.startswith(prefix):reply=line()
                return reply
            def write(wire):
                nonlocal wire_changed
                parts=wire.split();cmd=parts[2].decode();sent.append(cmd)
                target=cmd=='boot-data' and int(parts[3])==offset_target
                if target:
                    data_ids.append(parts[1]);fault_ids.add(parts[1])
                    # Not accepted: entire first request ID is lost before console.
                    if mode in ('notaccepted','forever') and (len(set(data_ids))==1 or mode=='forever'):return
                    # Native duplicate ID with a corrupted retry is an actual
                    # console ERR22 while actual cdev still contains the first block.
                    if mode=='err22' and len(data_ids)>1 and len(set(data_ids))==1:
                        wire=wire.replace(b'boot-data '+parts[3]+b' ',b'boot-data '+str(offset_target+1).encode()+b' ',1)
                    if mode=='rejectfirst' and len(set(data_ids))==1:
                        wire=wire.replace(parts[4],b'!!!!',1)
                reply=roundtrip(wire)
                if target and mode not in ('rejectfirst','notaccepted','forever'):
                    if mode=='err22' and len(data_ids)>1:pass
                    else:return # Actual data accepted (or partial-failed), ACK lost.
                if cmd=='status' and mode.startswith('status:'):
                    field,value=mode[7:].split(':');tokens=reply.split()
                    tokens=[(field+'='+value).encode() if t.startswith((field+'=').encode()) else t for t in tokens]
                    reply=b' '.join(tokens)+b'\n';wire_changed=True
                if cmd=='status' and mode=='statuslost':return
                if cmd=='status' and mode=='unknown':reply=b'!A04 '+parts[1]+b' OK unknown\n'
                pending.extend(reply)
            def read():
                data=bytes(pending);pending.clear();return data
            client=uart.Client(write,read,timeout=.01,retries=1,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),log=io.StringIO())
            try:
                if success:self.assertEqual(client.upload(image),'boot-write-verified')
                else:
                    with self.assertRaises(uart.ProtocolError):client.upload(image,reboot=True)
                state=roundtrip(b'TEST_STATE\n').decode()
                if success:self.assertIn('writes=3 flags=2',state);self.assertIn('boot-commit',sent)
                else:
                    self.assertIn('writes=0 ',state);self.assertNotIn('reboot',sent)
                    if mode!='corruptstage':self.assertNotIn('boot-commit',sent)
                self.assertIn('status',sent)
                if mode in ('notaccepted','rejectfirst'):
                    self.assertEqual(len(set(data_ids)),2) # Fresh ID on same block.
                if mode=='forever':self.assertEqual(len(set(data_ids)),uart.DATA_RECONCILE_RETRIES+1)
                if mode.startswith('status:'):self.assertTrue(wire_changed)
                return sent,state
            finally:
                server.stdin.close();server.wait(timeout=2)
                stderr=server.stderr.read();self.assertEqual(server.returncode,0,stderr)
                server.stdout.close();server.stderr.close()

    def test_actual_accepted_lost_all_acks_reconciles_next_offset(self):self.scenario('lost',success=True)
    def test_final_short_block_accepted_lost_ack_reconciles(self):self.scenario('lost',success=True,offset_target=130*1024-256)
    def test_actual_accepted_then_same_id_corruption_err22_reconciles(self):self.scenario('err22',success=True)
    def test_actual_not_accepted_resends_identical_block_with_fresh_id(self):self.scenario('notaccepted',success=True)
    def test_native_err22_without_acceptance_resends_fresh_id(self):self.scenario('rejectfirst',success=True)
    def test_bounded_resends_stop_without_commit(self):self.scenario('forever')
    def test_actual_kernel_partial_uiomove_failed_status_blocks_commit(self):self.scenario('partial')
    def test_unknown_partial_backwards_failed_wrong_image_status_blocks_commit(self):
        for mode,target in [('unknown',0),('statuslost',0),('status:staged:768',0),('status:staged:1',0),('status:staged:0',384),('status:flags:4',0),('status:flags:3',0),('status:image:8',0),('status:partition:8',0),('status:version:2',0)]:
            with self.subTest(mode=mode):self.scenario(mode,offset_target=target)
    def test_staged_offset_is_not_integrity_kernel_whole_sha_prevents_flash(self):self.scenario('corruptstage')

if __name__=='__main__':unittest.main()
