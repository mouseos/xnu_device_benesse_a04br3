# SPDX-License-Identifier: BSD-2-Clause-Patent
import pathlib,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class Console(unittest.TestCase):
 def test_actual_protocol_and_retry(self):
  source=r'''
#define A04_CONSOLE_TEST 1
#define open mockopen
#define close mockclose
#define write mockwrite
#define ioctl mockioctl
#include "SOURCE"
#include <assert.h>
#include <stdarg.h>
#include <errno.h>
int *__error(void){return &errno;}
static unsigned writes,begins,commits,closes;static int commit_error,write_error,short_write;
int mockopen(const char*p,int f,...){(void)p;(void)f;return 9;}
int mockclose(int fd){assert(fd==9);closes++;return 0;}
ssize_t mockwrite(int fd,const void*p,size_t n){assert(fd==9);assert(n<=4);(void)p;writes++;if(write_error){errno=5;return -1;}if(short_write){short_write=0;write_error=1;return 2;}return (ssize_t)n;}
int mockioctl(int fd,unsigned long cmd,...){assert(fd==9);va_list ap;va_start(ap,cmd);void*p=va_arg(ap,void*);va_end(ap);
 if(cmd==BEGIN){struct boot_begin*b=p;assert(b->image_bytes==4);begins++;return 0;}
 if(cmd==INFO){struct boot_info*b=p;b->version=1;b->partition_bytes=128*1024*1024;return 0;}
 if(cmd==COMMIT){commits++;if(commit_error){errno=5;return -1;}return 0;}
 return 0;}
int main(void){char r[512];
 command("@A04 a boot-begin 4 0000000000000000000000000000000000000000000000000000000000000000",r);assert(begins==1);
 command("@A04 a boot-begin 4 0000000000000000000000000000000000000000000000000000000000000000",r);assert(begins==1);
 command("@A04 a boot-abort",r);assert(strstr(r,"ERR 22"));
 command("@A04 b boot-data 0 dGVzdA==",r);assert(writes==1&&staged==4);
 command("@A04 b boot-data 0 dGVzdA==",r);assert(writes==1&&strstr(r,"offset=4"));
 command("@A04 c boot-data 0 dGVzdA==",r);assert(writes==1&&strstr(r,"ERR"));
 command("@A04 d boot-data 4 dGVzdB==",r);assert(writes==1&&strstr(r,"ERR"));
 commit_error=1;assert(!command("@A04 e boot-commit",r));assert(commits==1&&strstr(r,"ERR 5"));
 commit_error=0;assert(!command("@A04 f boot-commit",r));assert(commits==1&&strstr(r,"ERR 22"));
 command("@A04 f2 boot-begin 4 0000000000000000000000000000000000000000000000000000000000000000",r);
 command("@A04 f3 boot-commit",r);assert(commits==1&&strstr(r,"ERR 22"));
 command("@A04 f4 boot-data 0 dGVzdA==",r);assert(staged==4);
 command("@A04 f5 boot-commit",r);assert(commits==2&&strstr(r,"boot-write-verified"));
 command("@A04 f5 boot-commit",r);assert(commits==2); /* cached ACK, no duplicate flash */
 command("@A04 p0 boot-begin 4 0000000000000000000000000000000000000000000000000000000000000000",r);
 short_write=1;command("@A04 p1 boot-data 0 dGVzdA==",r);assert(staged==2&&upload_broken&&strstr(r,"ERR 5"));
 command("@A04 p2 boot-commit",r);assert(commits==2&&strstr(r,"ERR 22"));
 command("@A04 p3 boot-data 2 c3Q=",r);assert(staged==2&&strstr(r,"ERR 22"));
 command("@A04 p4 boot-abort",r);assert(bootfd==-1&&!staged&&!expected_image_bytes&&!upload_broken);
 command("@A04 p5 boot-commit",r);assert(commits==2&&strstr(r,"ERR 9"));
 assert(closes>=3);
 command("@A04 g status",r);assert(strstr(r,"partition=134217728"));
 u64 v;assert(number("18446744073709551616",&v));assert(number("-1",&v));
 unsigned char b[384];assert(decode("dGVzdA==",b)==4);assert(decode("a==a",b)<0);assert(decode("a!aa",b)<0);
 assert(!command("garbage",r));return 0;}
'''.replace('SOURCE',str(ROOT/'tools/uart-console.c')).replace('#include <errno.h>','#include <errno.h>\n#include <string.h>')
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'test.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
class Terminal(unittest.TestCase):
 def test_canonical_transport_preserves_control_bytes_without_echo(self):
  import ctypes,errno,os,pty,select,termios
  source=(ROOT/'tools/uart-console.c').read_text()
  body=source.split('static int configure_uart(int fd){',1)[1].split('\nstatic void console(void)',1)[0]
  unit='#include <termios.h>\n#include <errno.h>\n#define ERRNO errno\nint configure_uart(int fd){'+body
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'terminal.c').write_text(unit)
   subprocess.run(['clang','-shared','-fPIC','-Wall','-Wextra','-Werror',str(p/'terminal.c'),'-o',str(p/'terminal.so')],check=True)
   lib=ctypes.CDLL(str(p/'terminal.so'));master,slave=pty.openpty()
   try:
    t=termios.tcgetattr(slave)
    t[0]|=termios.BRKINT|termios.IXON|termios.IXOFF
    t[3]|=termios.ECHO|termios.ISIG|termios.IEXTEN
    termios.tcsetattr(slave,termios.TCSANOW,t)
    self.assertEqual(lib.configure_uart(slave),0)
    t=termios.tcgetattr(slave)
    self.assertTrue(t[3]&termios.ICANON)
    self.assertTrue(t[0]&termios.IGNBRK)
    self.assertFalse(t[0]&(termios.BRKINT|termios.IXON|termios.IXOFF))
    self.assertFalse(t[3]&(termios.ECHO|termios.ISIG|termios.IEXTEN))
    packet=b'@A04 test \x03\x1a\x1c status\r'
    os.write(master,packet)
    self.assertTrue(select.select([slave],[],[],1)[0])
    self.assertEqual(os.read(slave,1024),packet[:-1]+b'\n')
    self.assertFalse(select.select([master],[],[],0)[0])
    self.assertEqual(lib.configure_uart(-1),errno.EBADF)
   finally:
    os.close(master);os.close(slave)

class Supervisor(unittest.TestCase):
 def test_actual_waitpid_signal_budget_and_staging_fd_lifetime(self):
  source=r'''
#define A04_CONSOLE_TEST 1
#define open mockopen
#define ioctl mockioctl
#define sleep mocksleep
#include "SOURCE"
#undef open
#undef ioctl
#undef sleep
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
int *__error(void){return &errno;}
struct shared {unsigned attempts,delays[3],sleeps,commits,begins;int mode;};
static struct shared *shared;
static struct sockaddr_un address;
int mockopen(const char *name,int flags,...){
 assert(!strcmp(name,"/dev/a04boot")&&flags==1);
 int fd=socket(AF_UNIX,SOCK_STREAM,0);assert(fd>=0);
 assert(connect(fd,(void *)&address,sizeof(address))==0);return fd;
}
int mockioctl(int fd,unsigned long cmd,...){
 assert(fd>=0);if(cmd==BEGIN)shared->begins++;if(cmd==COMMIT)shared->commits++;return 0;
}
unsigned int mocksleep(unsigned int n){assert(shared->sleeps<3);shared->delays[shared->sleeps++]=n;return 0;}
static void child_console(void){
 unsigned attempt=++shared->attempts;char r[512];
 assert(bootfd==-1 && !staged && !expected_image_bytes && !upload_broken && !last_line[0]);
 command("@A04 stale boot-commit",r);assert(strstr(r,"ERR 9"));
 command("@A04 resume boot-data 4 dGVzdA==",r);assert(strstr(r,"ERR"));
 command("@A04 begin boot-begin 8 0000000000000000000000000000000000000000000000000000000000000000",r);
 command("@A04 data boot-data 0 dGVzdA==",r);assert(staged==4);
 command("@A04 partial boot-commit",r);assert(strstr(r,"ERR 22"));
 if(shared->mode==124)_exit(124);
 if(shared->mode==120)_exit(120);
 if(shared->mode==121)_exit(121);
 if(shared->mode==0)_exit(0);
 if(shared->mode==122)_exit(122);
 if(shared->mode==2 && attempt==2)_exit(0);
 assert(raise(SIGINT)==0);_exit(99);
}
int main(int argc,char **argv){
 assert(argc==2);char *dir=getenv("A04_TEST_DIR");assert(dir);
 shared=mmap(0,sizeof(*shared),PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);assert(shared!=MAP_FAILED);
 shared->mode=atoi(argv[1]);
 int listener=socket(AF_UNIX,SOCK_STREAM,0);assert(listener>=0);
 address.sun_family=AF_UNIX;snprintf(address.sun_path,sizeof(address.sun_path),"%s/stage.sock",dir);
 assert(bind(listener,(void *)&address,sizeof(address))==0 && listen(listener,8)==0);
 int result=supervise_console(child_console);
 unsigned expected=shared->mode==2?2:shared->mode==3||shared->mode==122?4:1;
 assert(shared->attempts==expected && shared->sleeps==expected-1);
 for(unsigned i=0;i<shared->sleeps;i++)assert(shared->delays[i]==(2u<<i));
 assert(!shared->commits && shared->begins==expected && bootfd==-1);
 if(expected==4)assert(result==127);else if(shared->mode==2)assert(result==0);else assert(result==shared->mode);
 /* Real process death closes every stage stand-in fd. The supervisor never
  * retained a writer: each socket yields the partial bytes and immediate EOF. */
 for(unsigned i=0;i<expected;i++){
  int fd=accept(listener,0,0);assert(fd>=0);char data[4];
  assert(read(fd,data,4)==4&&!memcmp(data,"test",4));
  assert(read(fd,data,1)==0);close(fd);
 }
 close(listener);unlink(address.sun_path);munmap(shared,sizeof(*shared));return 0;
}
'''.replace('SOURCE',str(ROOT/'tools/uart-console.c'))
  import os
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'supervisor.c').write_text(source)
   subprocess.run(['clang','-std=c11','-D_GNU_SOURCE','-Wall','-Wextra','-Werror',
                   '-Wno-unused-function','-fsanitize=address,undefined',str(p/'supervisor.c'),'-o',str(p/'supervisor')],check=True)
   for mode in (2,3,122,124,120,121,0):
    subprocess.run([str(p/'supervisor'),str(mode)],env=dict(os.environ,A04_TEST_DIR=d),timeout=5,check=True)

 def test_native_pid1_exec_even_if_supervisor_fork_fails(self):
  source=r'''
#define A04_CONSOLE_TEST 1
#define fork mockfork
#define execv mockexecv
#include "SOURCE"
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <setjmp.h>
int *__error(void){return &errno;}
static int forkresult,called;static jmp_buf escape;
int mockfork(void){return forkresult;}
int mockexecv(const char *path,char *const *args){
 assert(!strcmp(path,"/sbin/launchd")&&!strcmp(args[0],path)&&!args[1]);called++;longjmp(escape,1);
}
static void unused_console(void){assert(0);}
int main(void){
 forkresult=-1;if(!setjmp(escape))launch_native_with_console(unused_console);assert(called==1);
 forkresult=42;if(!setjmp(escape))launch_native_with_console(unused_console);assert(called==2);
 return 0;
}
'''.replace('SOURCE',str(ROOT/'tools/uart-console.c'))
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'launcher.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                   '-fsanitize=address,undefined',str(p/'launcher.c'),'-o',str(p/'launcher')],check=True)
   subprocess.run([str(p/'launcher')],check=True)


class ConsoleSetup(unittest.TestCase):
 def test_actual_console_setup_failure_never_announces_ready(self):
  unit=(ROOT/'tools/uart-console.c').read_text()
  console=unit[unit.index('static void console(void){'):unit.index('\nint main(int argc')]
  source=r"""
#define A04_CONSOLE_TEST 1
#define open mockopen
#define close mockclose
#define setsid mocksetsid
#define dup2 mockdup2
#define ioctl mockioctl
#define write mockwrite
#define read mockread
#define sleep mocksleep
#define _exit mockexit
#include "SOURCE"
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <setjmp.h>
int *__error(void){return &errno;}
static jmp_buf escape;static int exitcode,mode,configures,closed;
static char output[4096];static size_t outputlen;
void mockexit(int n){exitcode=n;longjmp(escape,1);}
int mockopen(const char *p,int flags,...){assert(!strcmp(p,"/dev/console")&&flags==2);return 5;}
int mockclose(int fd){assert(fd==5);closed++;return 0;}
int mocksetsid(void){return 1;}
int mockdup2(int fd,int target){assert(fd==5);if(mode==2){errno=9;return -1;}return target;}
int mockioctl(int fd,unsigned long cmd,...){(void)fd;(void)cmd;return 0;}
ssize_t mockwrite(int fd,const void *p,size_t n){assert(fd==1&&outputlen+n<sizeof(output));memcpy(output+outputlen,p,n);outputlen+=n;return (ssize_t)n;}
ssize_t mockread(int fd,void *p,size_t n){assert(mode==1&&fd==0&&n==1);(void)p;errno=5;return -1;}
unsigned int mocksleep(unsigned int n){assert(n==1);return 0;}
static int configure_uart(int fd){assert(fd==0||fd==5);configures++;return mode==0||configures==2?22:0;}
""".replace('SOURCE',str(ROOT/'tools/uart-console.c'))+console+r"""
int main(void){
 if(!setjmp(escape))console();assert(exitcode==124&&configures==1&&!strstr(output,"READY"));
 mode=1;exitcode=configures=closed=0;outputlen=0;memset(output,0,sizeof(output));
 if(!setjmp(escape))console();assert(exitcode==124&&configures==2&&closed==2);
 mode=2;exitcode=configures=0;outputlen=0;memset(output,0,sizeof(output));
 if(!setjmp(escape))console();assert(exitcode==121&&!configures&&!strstr(output,"READY"));return 0;
}
"""
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'setup.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                   '-fsanitize=address,undefined',str(p/'setup.c'),'-o',str(p/'setup')],check=True)
   subprocess.run([str(p/'setup')],check=True)

if __name__=='__main__':unittest.main()
