# SPDX-License-Identifier: MIT
"""Host delta plan -> actual native console -> actual kernel boot cdev/mock disk."""
import ast
import io
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
TOOLS=Path(__file__).resolve().parents[1]/'tools'
KERNEL=TOOLS.parents[1]/'kernel'
sys.path.insert(0,str(TOOLS));import uart_client as uart

def assignment(path,name):
    tree=ast.parse(path.read_text())
    return next(n.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))

class DeltaUpload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();p=Path(cls.tmp.name);cls.exe=p/'server'
        source=(KERNEL/'bsd/dev/a04br3_msdc.c').read_text()
        body=source[source.index('static LCK_MTX_DECLARE(boot_lock'):source.index('static const struct cdevsw boot_cdevsw')]
        harness=eval(compile(ast.Expression(assignment(KERNEL/'build-support/tests/test_boot_update.py','harness')),'k','eval'),{'ROOT':KERNEL,'body':body})
        harness=harness[:harness.index('int main(void){')].replace('image[132*1024]','image[130*1024]')
        bridge=eval(compile(ast.Expression(assignment(TOOLS.parent/'tests/test_uart_upload_reconcile.py','bridge')),'b','eval'),{'TOOLS':TOOLS})
        bridge=bridge.replace('#define ioctl bridge_ioctl','#define ioctl bridge_ioctl\n#define lseek bridge_lseek').replace('#undef ioctl','#undef ioctl\n#undef lseek')
        bridge=bridge.replace('void bridge_sync(void){}','void bridge_sync(void){}\noff_t bridge_lseek(int fd,off_t off,int whence){assert(fd==9&&whence==0&&off>=0);file_offset=(uint64_t)off;return off;}')
        bridge=bridge.replace('cmd==ABORT?A04_BOOT_ABORT:cmd','cmd==ABORT?A04_BOOT_ABORT:cmd==COPY?A04_BOOT_COPY:cmd')
        bridge=bridge.replace('FILE *f=fopen(argv[1],"wb");','uint32_t rng=1234567;for(unsigned i=4616;i<sizeof(image);i++){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;image[i]=(uint8_t)rng;}\n FILE *f=fopen(argv[1],"wb");')
        assert 'COPY?A04_BOOT_COPY' in bridge
        (p/'server.c').write_text(harness+bridge)
        subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-deprecated-declarations','-fsanitize=address,undefined',str(p/'server.c'),'-lcrypto','-ldl','-o',str(cls.exe)],check=True)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def run_case(self,wrong_base):
        with tempfile.TemporaryDirectory() as d:
            image=Path(d)/'boot';server=subprocess.Popen([str(self.exe),str(image),'normal'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
            def line():
                data=b''
                while not data.endswith(b'\n'):
                    self.assertTrue(select.select([server.stdout],[],[],5)[0]);b=server.stdout.read(1);self.assertTrue(b);data+=b
                return data
            self.assertEqual(line(),b'READY\n');pending=bytearray();sent=[]
            def exchange(wire):
                server.stdin.write(wire);server.stdin.flush();prefix=b'STATE ' if wire==b'TEST_STATE\n' else b'!A04 '+wire.split()[1]+b' '
                r=line()
                while not r.startswith(prefix):r=line()
                return r
            def write(wire):sent.append(wire.split()[2].decode());pending.extend(exchange(wire))
            def read():r=bytes(pending);pending.clear();return r
            now=[0.0]
            client=uart.Client(write,read,timeout=.01,retries=1,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),log=io.StringIO())
            try:
                self.assertEqual(client.upload(image),'boot-write-verified')
                self.assertIn(b'writes=3 flags=2',exchange(b'TEST_STATE\n'))
                old=image.read_bytes();new=bytearray(old)
                for at in (5000,40000,90000):new[at]^=0x5a
                new[60000:64096]=old[20480:24576]  # moved block found by the index
                newp=Path(d)/'new';newp.write_bytes(new)
                base=Path(d)/'base'
                if wrong_base:
                    b=bytearray(old);b[10000]^=1;b[70000:74096]=new[60000:64096];base.write_bytes(b)
                else:base.write_bytes(old)
                exchange(b'@A04 abort0 boot-abort\n');sent.clear()
                if wrong_base:
                    # Plan copies a block the disk does not hold; whole-image SHA rejects COMMIT.
                    b2=bytearray(old);b2[50000:54096]=bytes(4096);base.write_bytes(b2)
                    new2=bytearray(new);new2[50000:54096]=bytes(4096);newp.write_bytes(new2)
                    with self.assertRaises(uart.ProtocolError):client.upload_delta(newp,base,reboot=True)
                    self.assertNotIn('reboot',sent)
                    self.assertIn(b'writes=3 ',exchange(b'TEST_STATE\n'))
                else:
                    self.assertEqual(client.upload_delta(newp,base),'boot-write-verified')
                    state=exchange(b'TEST_STATE\n');self.assertIn(b'writes=6 flags=2',state)
                    self.assertIn('boot-dcopy',sent)
                    self.assertLess(sent.count('boot-data'),len(new)//384//4)
            finally:
                server.stdin.close();server.wait(timeout=5);err=server.stderr.read();self.assertEqual(server.returncode,0,err);server.stdout.close();server.stderr.close()
    def test_delta_reproduces_verified_image_with_few_literals(self):self.run_case(False)
    def test_wrong_current_partition_never_writes_boot(self):self.run_case(True)
if __name__=='__main__':unittest.main()
