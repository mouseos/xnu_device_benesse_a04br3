# SPDX-License-Identifier: MIT
"""Host optionalgzip -> actual native libz/parser -> actual cdev SHA/mockdisk.
Explicit dlopen path adapter for Linux; no hardware or fabricated crypto ACK.
"""
import ast
import base64
import importlib.util
import io
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib
TOOLS=Path(__file__).resolve().parents[1]/'tools'
KERNEL=TOOLS.parents[1]/'kernel'
sys.path.insert(0,str(TOOLS));import uart_client as uart

class GzipUpload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();p=Path(cls.tmp.name);cls.exe=p/'server'
        source=(KERNEL/'bsd/dev/a04br3_msdc.c').read_text()
        body=source[source.index('static LCK_MTX_DECLARE(boot_lock'):source.index('static const struct cdevsw boot_cdevsw')]
        def assignment(path,name):
            tree=ast.parse(path.read_text())
            return next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))
        harness=eval(compile(ast.Expression(assignment(KERNEL/'build-support/tests/test_boot_update.py','harness')),'trusted-kernel-fixture','eval'),{'ROOT':KERNEL,'body':body})
        harness=harness[:harness.index('int main(void){')].replace('image[132*1024]','image[130*1024]')
        bridge=eval(compile(ast.Expression(assignment(TOOLS.parent/'tests/test_uart_upload_reconcile.py','bridge')),'trusted-console-bridge','eval'),{'TOOLS':TOOLS})
        bridge=bridge.replace('#define A04_CONSOLE_TEST 1','#define A04_CONSOLE_TEST 1\nstatic int loader_absent;\n#define dlopen bridge_dlopen')
        bridge=bridge.replace('#undef digest', '#undef digest\n#undef dlopen\nextern void *dlopen(const char *,int);\nvoid *bridge_dlopen(const char *p,int f){assert(!strcmp(p,"/usr/lib/libz.1.dylib"));return loader_absent?0:dlopen("libz.so.1",f);}')
        bridge=bridge.replace('assert(argc==3);','assert(argc==3);loader_absent=!strcmp(argv[2],"absent");')
        bridge=bridge.replace('FILE *f=fopen(argv[1],"wb");', 'uint32_t rng=1234567;for(unsigned i=4616;i<sizeof(image);i++){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;image[i]=(uint8_t)rng;}\n FILE *f=fopen(argv[1],"wb");')
        (p/'server.c').write_text(harness+bridge)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-deprecated-declarations','-fsanitize=address,undefined',str(p/'server.c'),'-lcrypto','-ldl','-o',str(cls.exe)],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def scenario(self,mode,success=False):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'boot';server=subprocess.Popen([str(self.exe),str(image),'absent' if mode=='absent' else 'normal'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
            def line():
                data=b''
                while not data.endswith(b'\n'):
                    self.assertTrue(select.select([server.stdout],[],[],2)[0]);b=server.stdout.read(1);self.assertTrue(b);data+=b
                return data
            self.assertEqual(line(),b'READY\n');pending=bytearray();sent=[];ids=[];now=[0.0];first_id=[None];wire_by_command={}
            def exchange(wire):
                server.stdin.write(wire);server.stdin.flush();prefix=b'STATE ' if wire==b'TEST_STATE\n' else b'!A04 '+wire.split()[1]+b' '
                result=line()
                while not result.startswith(prefix):result=line()
                return result
            def write(wire):
                fields=wire.split();cmd=fields[2].decode();sent.append(cmd)
                wire_by_command.setdefault(cmd,[]).append(wire)
                if mode=='txfail' and cmd=='boot-zdata':raise uart.AckTimeout('injected TX deadline')
                reply=exchange(wire)
                for verb in ('boot-zbegin','boot-zfinish','boot-commit'):
                    if cmd==verb and mode in (verb+'-firstlost',verb+'-alllost'):
                        if mode.endswith('-alllost') or len(wire_by_command[cmd])==1:return
                if cmd=='boot-zdata':
                    if first_id[0] is None:first_id[0]=fields[1]
                    if fields[1]==first_id[0]:
                        ids.append(fields[1])
                        if mode=='dropfirst' and len(ids)==1:return
                        if mode=='lost':return
                        if mode=='err22':reply=b'!A04 '+fields[1]+b' ERR 22\n'
                        if mode=='badraw':reply=b'!A04 '+fields[1]+b' OK offset='+str(int(fields[3])+len(base64.b64decode(fields[5]))).encode()+b' raw=999999999\n'
                        if mode=='badoffset':reply=b'!A04 '+fields[1]+b' OK offset=1 raw=0\n'
                if cmd=='boot-zfinish' and mode=='badfinish':reply=b'!A04 '+fields[1]+b' OK offset=0 raw=0\n'
                pending.extend(reply)
            def read():r=bytes(pending);pending.clear();return r
            client=uart.Client(write,read,timeout=.01,retries=1,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),log=io.StringIO())
            try:
                if success:self.assertEqual(client.upload(image,compression='gzip'),'boot-write-verified')
                else:
                    with self.assertRaises(uart.ProtocolError):client.upload(image,compression='gzip',reboot=True)
                state=exchange(b'TEST_STATE\n')
                if success:self.assertIn(b'writes=3 flags=2',state);self.assertIn('boot-zfinish',sent);self.assertIn('boot-commit',sent)
                else:
                    if mode=='boot-commit-alllost':
                        self.assertIn(b'writes=3 flags=2',state) # Device committed, host cannot declare it.
                    else:
                        self.assertIn(b'writes=0 ',state);self.assertNotIn('boot-commit',sent)
                    self.assertNotIn('reboot',sent)
                self.assertNotIn('boot-begin',sent);self.assertNotIn('boot-data',sent);self.assertNotIn('status',sent)
                if mode in ('dropfirst','lost'):self.assertEqual(ids,[first_id[0]]*2)
                if mode.endswith(('-firstlost','-alllost')):
                    verb=mode.rsplit('-',1)[0];self.assertEqual(len(wire_by_command[verb]),2)
                    self.assertEqual(wire_by_command[verb][0],wire_by_command[verb][1])
                if mode=='txfail':self.assertEqual(sent.count('boot-zdata'),1)
            finally:
                server.stdin.close();server.wait(timeout=2);err=server.stderr.read();self.assertEqual(server.returncode,0,err);server.stdout.close();server.stderr.close()
    def test_real_native_gzip_and_kernel_sha_readback(self):self.scenario('normal',True)
    def test_cached_same_id_ack_retry_no_double_inflate(self):self.scenario('dropfirst',True)
    def test_missing_libz_no_automatic_raw_fallback(self):self.scenario('absent')
    def test_unrecoverable_ack_err22_and_malformed_counters_never_commit(self):
        for mode in ('lost','err22','badraw','badoffset','badfinish'):
            with self.subTest(mode=mode):self.scenario(mode)
    def test_begin_finish_commit_cached_ack_retry_does_not_repeat_effect(self):
        for command in ('boot-zbegin','boot-zfinish','boot-commit'):
            with self.subTest(command=command):self.scenario(command+'-firstlost',True)
    def test_exhausted_begin_finish_commit_acks_never_reboot_or_claim_success(self):
        for command in ('boot-zbegin','boot-zfinish','boot-commit'):
            with self.subTest(command=command):self.scenario(command+'-alllost')
    def test_tx_timeout_never_reconciles_status_retries_or_commits(self):self.scenario('txfail')

    def test_cli_raw_default_and_gzip_explicit_route_without_uart_sends(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);wire=root/'uart';wire.touch();raw=root/'collector';raw.write_bytes(b'existing log')
            seen=[]
            def upload(path,**kwargs):seen.append(kwargs['compression']);return 'route-only'
            with mock.patch.object(uart.Client,'upload',side_effect=upload),mock.patch('sys.stdout',new_callable=io.StringIO):
                for option in ([],['--gzip']):
                    self.assertEqual(uart.main(['--uart',str(wire),'--raw-log',str(raw),'--log',str(root/'metadata'),'upload',str(root/'image'),*option]),0)
            self.assertEqual(seen,['raw','gzip']);self.assertEqual(wire.read_bytes(),b'')

    def test_explicit_wire_validation_and_raw_default(self):
        for cmd,args in [('boot-zbegin',('1','6144','0'*64)),('boot-zbegin',('100','6145','0'*64)),('boot-zdata',('0','00000000','dGVzdA==')),('boot-zfinish',('extra',))]:
            with self.assertRaises(ValueError):uart.request_line('x',cmd,args)
        events=[];client=uart.Client(lambda data:None,lambda:b'')
        client.request=lambda command,*args:events.append(command) or 'unused'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad';p.write_bytes(b'x')
            with self.assertRaises(ValueError):client.upload(p,compression='gzip')
            self.assertEqual(events,[])

if __name__=='__main__':unittest.main()
