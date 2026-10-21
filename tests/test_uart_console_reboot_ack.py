# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Run the actual console loop under faulted ACK writes, never a real reboot."""
from pathlib import Path
import subprocess
import tempfile
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
class RebootACK(unittest.TestCase):
    def test_reboot_requires_entire_ack(self):
        unit=(TOOLS/'uart-console.c').read_text()
        console=unit[unit.index('static int console_usb;'):unit.index('static int configure_uart(int fd){')]+unit[unit.index('static void console(void){'):unit.index('\nint main(int argc')]
        source=r'''
#define A04_CONSOLE_TEST 1
#define open mockopen
#define close mockclose
#define setsid mocksetsid
#define dup2 mockdup2
#define ioctl mockioctl
#define write mockwrite
#define read mockread
#define sleep mocksleep
#define sync mocksync
#define reboot mockreboot
#define _exit mockexit
#include "SOURCE"
#include <assert.h>
#include <errno.h>
#include <string.h>
#include <setjmp.h>
static jmp_buf done;
static unsigned mode,at,reboots,ackwrites,drains;
static int ack_started;
static char output[4096];static size_t used;
int *__error(void){return &errno;}
void mockexit(int code){(void)code;assert(0);}
int mockopen(const char *name,int flags,...){assert(!strcmp(name,mode>=4?"/dev/a04usb":"/dev/console")&&flags==2);return 5;}
int mockclose(int fd){assert(fd==5);return 0;}
int mocksetsid(void){return 1;}
int mockdup2(int fd,int target){assert(fd==5);return target;}
int mockioctl(int fd,unsigned long cmd,...){(void)fd;if(cmd==0x20005570UL){drains++;if(mode==5){errno=EIO;return -1;}}return 0;}
void mocksync(void){}
int mockreboot(int flags){assert(!flags);reboots++;return 0;}
unsigned int mocksleep(unsigned int n){(void)n;assert(0);return 0;}
static int configure_uart(int fd){assert(fd==0);return 0;}
ssize_t mockwrite(int fd,const void *data,size_t bytes){
 assert(fd==1);const char *p=data;
 if(bytes>=6 && !memcmp(p,"\n!A04 ",6))ack_started=1;
 if(ack_started){
  ackwrites++;
  if(mode==1 || (mode==2 && ackwrites>1)){errno=EIO;return -1;}
  if(mode==3){errno=EINTR;return -1;}
  if(mode==2 && ackwrites==1)bytes=5;
 }
 assert(used+bytes<sizeof(output));memcpy(output+used,p,bytes);used+=bytes;return (ssize_t)bytes;
}
ssize_t mockread(int fd,void *out,size_t bytes){
 const char *request="@A04 check reboot\n";assert(fd==0&&bytes==1);
 if(!request[at])longjmp(done,1);
 *(char *)out=request[at++];return 1;
}
'''.replace('SOURCE',str(TOOLS/'uart-console.c'))+console+r'''
int main(void){
 for(mode=0;mode<6;mode++){
  console_usb=mode>=4;drains=0;
  at=reboots=ackwrites=used=0;ack_started=0;memset(output,0,sizeof(output));
  memset(last_line,0,sizeof(last_line));memset(last_reply,0,sizeof(last_reply));memset(last_id,0,sizeof(last_id));
  if(!setjmp(done))console();
  assert(ackwrites && reboots==((mode==0||mode==4)?1u:0u));
  if(!mode)assert(strstr(output,"!A04 check OK restarting\n"));
  assert(drains==(mode>=4?1u:0u));
  if(mode==5)assert(strstr(output,"reboot blocked: USB ACK not drained"));
 }
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(source)
            subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                            '-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)

if __name__=='__main__':unittest.main()
