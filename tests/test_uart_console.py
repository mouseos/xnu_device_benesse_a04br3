# SPDX-License-Identifier: BSD-2-Clause-Patent
import pathlib,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class Console(unittest.TestCase):
 def test_actual_protocol_and_retry(self):
  source=r'''
#define A04_CONSOLE_TEST 1
#define open mockopen
#define write mockwrite
#define ioctl mockioctl
#include "SOURCE"
#include <assert.h>
#include <stdarg.h>
#include <errno.h>
int *__error(void){return &errno;}
static unsigned writes,begins,commits;static int commit_error;
int mockopen(const char*p,int f,...){(void)p;(void)f;return 9;}
ssize_t mockwrite(int fd,const void*p,size_t n){assert(fd==9);assert(n==4);assert(!memcmp(p,"test",4));writes++;return (ssize_t)n;}
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
 commit_error=0;assert(!command("@A04 f boot-commit",r));assert(commits==2&&strstr(r,"boot-write-verified"));
 command("@A04 g status",r);assert(strstr(r,"partition=134217728"));
 u64 v;assert(number("18446744073709551616",&v));assert(number("-1",&v));
 unsigned char b[384];assert(decode("dGVzdA==",b)==4);assert(decode("a==a",b)<0);assert(decode("a!aa",b)<0);
 assert(!command("garbage",r));return 0;}
'''.replace('SOURCE',str(ROOT/'tools/uart-console.c')).replace('#include <errno.h>','#include <errno.h>\n#include <string.h>')
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d);(p/'test.c').write_text(source)
   subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
if __name__=='__main__':unittest.main()
