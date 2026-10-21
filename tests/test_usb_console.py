# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual native USB C framing/cached replay + canonical console/cdev on pipes.
MMIO/USB FIFOs/eMMC are explicit host models, no hardware touched.
"""
import ast
import io
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import zlib
TOOLS=Path(__file__).resolve().parents[1]/'tools';KERNEL=TOOLS.parents[1]/'kernel';USB=KERNEL/'iokit/Kernel/arm/usb'
sys.path.insert(0,str(TOOLS));import usb_console as host;import uart_client as uart

class USBConsole(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name);cls.exe=p/'usbserver'
  source=(KERNEL/'bsd/dev/a04br3_msdc.c').read_text();body=source[source.index('static LCK_MTX_DECLARE(boot_lock'):source.index('static const struct cdevsw boot_cdevsw')]
  def expr(path,name):
   return next(n.value for n in ast.walk(ast.parse(path.read_text())) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))
  h=eval(compile(ast.Expression(expr(KERNEL/'build-support/tests/test_boot_update.py','harness')),'trusted-fixture','eval'),{'ROOT':KERNEL,'body':body});h=h[:h.index('int main(void){')]
  h=h.replace('printf("A04BR3 BOOT UPDATE:', 'fprintf(stderr,"A04BR3 BOOT UPDATE:')
  bridge=eval(compile(ast.Expression(expr(TOOLS.parent/'tests/test_uart_upload_reconcile.py','bridge')),'trusted-console','eval'),{'TOOLS':TOOLS});bridge=bridge[:bridge.index('int main(int argc')]
  bridge=bridge.replace('#define A04_CONSOLE_TEST 1','static unsigned char txout[65536];static unsigned txbytes;\n#define A04_CONSOLE_TEST 1\n#define dlopen usb_test_dlopen')
  bridge=bridge.replace('#undef digest','#undef digest\n#undef dlopen\nextern void *dlopen(const char *,int);\nvoid *usb_test_dlopen(const char *p,int f){assert(!strcmp(p,"/usr/lib/libz.1.dylib"));return dlopen("libz.so.1",f);}\nextern int open(const char *,int,...),close(int);')
  bridge=bridge.replace('assert(!strcmp(name,"/dev/a04boot")&&flags==1);','if(strcmp(name,"/dev/a04boot"))return open(name,flags);assert(flags==1);')
  bridge=bridge.replace('int bridge_close(int fd){assert(fd==9);','int bridge_close(int fd){if(fd!=9)return close(fd);')
  bridge=bridge.replace('assert(fd==9);struct uio u=', 'if(fd==1){assert(txbytes+bytes<=sizeof(txout));memcpy(txout+txbytes,data,bytes);txbytes+=(unsigned)bytes;return (ssize_t)bytes;}assert(fd==9);struct uio u=')
  more=r'''
#include "A04BR3USBConsole.h"
static struct a04_usb_session session;
static struct a04_usb_console console;
static unsigned pushes,pulls,commands,busy,restarts,wrongcommit;
static char input[1024];static unsigned inputbytes;
static void emit(const void *data,unsigned n){assert(txbytes+n<=sizeof(txout));memcpy(txout+txbytes,data,n);txbytes+=n;}
static int push(void *ctx,const uint8_t *data,unsigned n){(void)ctx;if(busy){busy--;return 1;}pushes++;
 for(unsigned i=0;i<n;i++){
  if(data[i]=='\n'){
   input[inputbytes]=0;char reply[512]={0};commands++;restarts+=(unsigned)command(input,reply);
   if(wrongcommit&&strstr(input," boot-commit")){char *p=strstr(reply,"boot-write-verified");if(p)strcpy(p,"committed\n");}
   emit(reply,(unsigned)strlen(reply));inputbytes=0;
  }else{assert(inputbytes+1<sizeof(input));input[inputbytes++]=(char)data[i];}
 }return 0;
}
static unsigned pull(void *ctx,uint8_t *data,unsigned cap){(void)ctx;pulls++;unsigned n=txbytes<cap?txbytes:cap;memcpy(data,txout,n);memmove(txout,txout+n,txbytes-n);txbytes-=n;return n;}
int main(int argc,char **argv){assert(argc==4);assert(proc_suser(&other)==EPERM);reset();busy=(unsigned)atoi(argv[2]);wrongcommit=(unsigned)atoi(argv[3]);
 FILE *imagefile=fopen(argv[1],"wb");assert(imagefile&&fwrite(image,1,sizeof(image),imagefile)==sizeof(image));assert(!fclose(imagefile));
 a04_usb_session_reset(&session);console.ops.context=0;console.ops.push_rx=push;console.ops.pull_tx=pull;a04_usb_console_reset(&console);
 emit("A04BR3_USBCONSOLE READY\n",23);
 for(;;){uint32_t bytes,mps;if(fread(&bytes,4,1,stdin)!=1)break;
  if(bytes==0xffffffff){uint32_t state[]={pushes,pulls,commands,writes,boot_flags,(uint32_t)boot_staged,restarts};assert(fwrite(state,sizeof(state),1,stdout)==1);fflush(stdout);continue;}
  assert(fread(&mps,4,1,stdin)==1&&bytes<=512);unsigned char req[512],reply[512];assert(fread(req,1,bytes,stdin)==bytes);unsigned written=0;
  int r=a04_usb_console_process(&session,&console,req,bytes,reply,sizeof(reply),mps,&written);if(r)written=0;
  assert(fwrite(&written,4,1,stdout)==1);assert(fwrite(reply,1,written,stdout)==written);fflush(stdout);
 }
 boot_reset();return 0;
}
'''
  (p/'usbserver.c').write_text(h+bridge+more)
  subprocess.run(['clang','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-misleading-indentation','-Wno-deprecated-declarations','-fsanitize=address,undefined','-I',str(USB),str(p/'usbserver.c'),'-lcrypto','-ldl','-o',str(cls.exe)],check=True)
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def start(self,d,mps=512,busy=0,wrong=0):
  image=Path(d)/'boot';server=subprocess.Popen([str(self.exe),str(image),str(busy),str(wrong)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
  def exact(n):
   data=b''
   while len(data)<n:
    self.assertTrue(select.select([server.stdout],[],[],3)[0],'C bridge timeout');part=os.read(server.stdout.fileno(),n-len(data));self.assertTrue(part,'C bridge exited');data+=part
   return data
  def exchange(frame,timeout=3000):
   server.stdin.write(struct.pack('<II',len(frame),mps)+frame);server.stdin.flush();return exact(struct.unpack('<I',exact(4))[0])
  def state():server.stdin.write(struct.pack('<I',0xffffffff));server.stdin.flush();return struct.unpack('<7I',exact(28))
  def cleanup():
   server.stdin.close();server.wait(timeout=3);err=server.stderr.read();self.assertEqual(server.returncode,0,err);server.stdout.close();server.stderr.close()
  self.addCleanup(cleanup);return image,exchange,state
 def test_actual_c_stdout_shell_gzip_and_existing_sha_bootwriter(self):
  with tempfile.TemporaryDirectory() as d:
   image,exchange,state=self.start(d,busy=3);stream=host.BulkStream(exchange,512);stream.handshake(time.monotonic()+3);out=bytearray();console=host.Console(stream,timeout=3,stdout=out.extend)
   console.command('help');console.command('cd',d);console.command('pwd');blob=b'actual cat\0binary\xff\n';path=Path(d)/'data';path.write_bytes(blob);console.command('ls',d);console.command('cat',str(path))
   self.assertIn(blob,out);self.assertIn(d.encode(),out);self.assertIn(b'A04BR3_USBCONSOLE READY',out)
   self.assertEqual(console.upload(image,gzip=True),'boot-write-verified');self.assertEqual(state()[3:5],(3,2))
 def test_fullspeed_stdout_and_cached_tx_rx_replay_no_duplicate_push_or_pop(self):
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d,mps=64);lost={7,8}
   def faults(frame,timeout):
    reply=exchange(frame,timeout);op=frame[6]
    if op in lost:lost.remove(op);raise TimeoutError('injected lost bulk reply')
    return reply
   stream=host.BulkStream(faults,64);stream.handshake(time.monotonic()+3);out=bytearray();console=host.Console(stream,timeout=3,stdout=out.extend);console.command('pwd')
   self.assertEqual(state()[2],1);self.assertEqual(bytes(out).count(b'shell-status=0'),1)
 def test_actual_c_negative_crc_replay_conflict_and_wrong_ack_no_reboot(self):
  with tempfile.TemporaryDirectory() as d:
   image,exchange,state=self.start(d,wrong=1)
   bad=bytearray(host.framing.encode(7,1,0,b'x'));bad[-1]^=1;self.assertEqual(exchange(bytes(bad)),b'');self.assertEqual(state()[0],0)
   req=host.framing.encode(7,1,0,b'');self.assertEqual(struct.unpack_from('<I',exchange(req),32)[0],4)
   valid=host.framing.encode(7,1,0,b'@A04 old shell pwd\n');first=exchange(valid)
   self.assertEqual(exchange(valid),first);self.assertEqual(state()[2],1)
   conflict=exchange(host.framing.encode(7,1,0,b'@A04 old shell help\n'))
   self.assertEqual(struct.unpack_from('<I',conflict,32)[0],2);self.assertEqual(state()[2],1)
   stream=host.BulkStream(exchange,512);stream.handshake(time.monotonic()+3);console=host.Console(stream,timeout=3)
   with self.assertRaises(uart.ProtocolError):console.upload(image,reboot=True)
   self.assertEqual(state()[3:5],(3,2));self.assertEqual(state()[6],0)
 def test_busy_deadline_and_response_crc_never_advance_or_resend_command(self):
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d,busy=100000);stream=host.BulkStream(exchange,512)
   with self.assertRaises(uart.AckTimeout):stream.send(b'@A04 x status\n',time.monotonic()+.03)
   self.assertEqual(state()[0],0)
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d)
   def corrupt(frame,timeout):
    out=bytearray(exchange(frame,timeout));out[-1]^=1;return out
   stream=host.BulkStream(corrupt,512)
   with self.assertRaises(uart.ProtocolError):stream.send(b'x',time.monotonic()+1)
   self.assertEqual(stream.seq,1);self.assertEqual(state()[0],1)

 def test_wrong_positive_gzip_commit_ack_cannot_reboot(self):
  with tempfile.TemporaryDirectory() as d:
   image,exchange,state=self.start(d,wrong=1);stream=host.BulkStream(exchange,512);stream.handshake(time.monotonic()+3)
   with self.assertRaises(uart.ProtocolError):host.Console(stream,timeout=3).upload(image,gzip=True,reboot=True)
   self.assertEqual(state()[3:5],(3,2));self.assertEqual(state()[6],0)
 def test_shell_translations_and_strict_native_arguments(self):
  calls=[]
  class C:
   def command(self,*args):calls.append(args)
   def upload(self,*args,**kw):calls.append((args,kw))
  for line in ('ls /','cat /file','cd /tmp','pwd','help','ps -A','/bin/ps -A','exec /bin/ps'):
   host.run_line(C(),line)
  self.assertEqual(calls[:5],[('ls','/'),('cat','/file'),('cd','/tmp'),('pwd',),('help',)])
  self.assertEqual(calls[5:],[('exec','/bin/ps','-A'),('exec','/bin/ps','-A'),('exec','/bin/ps')])
  for args in ((),('cd',),('pwd','/'),('cat',),('rm','/'),('ls','a','b')):
   with self.assertRaises(ValueError):uart.request_line('x','shell',args)
 def test_exact_identity_wait_is_finite_and_ignores_stock_or_unconfigured(self):
  class IF(list):
   bInterfaceClass=255;bAlternateSetting=0
  class EP:
   bmAttributes=2;wMaxPacketSize=512
   def __init__(self,address):self.bEndpointAddress=address
  class Dev:
   idVendor=host.VID;idProduct=host.PID;bcdDevice=host.BCD_DEVICE;product=host.PRODUCT
   def get_active_configuration(self):return {(0,0):IF((EP(1),EP(129)))}
  good=Dev();stock=Dev();stock.product='stock adb';wrong=Dev();wrong.bcdDevice=2
  now=[0.0];events=[];seen=[0]
  def find():seen[0]+=1;return [stock,wrong] if seen[0]<3 else [good]
  result=host.wait_device(find,3,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n),event=lambda **kw:events.append(kw))
  self.assertEqual(result,(good,512));self.assertEqual(now[0],2);self.assertEqual(len(events),1)
  with self.assertRaises(uart.AckTimeout):host.wait_device(lambda:[stock],1,clock=lambda:now[0],sleep=lambda n:now.__setitem__(0,now[0]+n))
 def test_valid_crc_but_bad_acceptance_count_stops_channel(self):
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d)
   def invalid(frame,timeout):
    reply=bytearray(exchange(frame,timeout));struct.pack_into('<I',reply,40,0)
    struct.pack_into('<I',reply,20,zlib.crc32(reply[32:]));reply[24:28]=bytes(4);struct.pack_into('<I',reply,24,zlib.crc32(reply[:32]));return reply
   stream=host.BulkStream(invalid,512)
   with self.assertRaises(uart.ProtocolError):stream.send(b'x',time.monotonic()+1)
   self.assertTrue(stream.poisoned);before=state()[0]
   with self.assertRaises(uart.ProtocolError):stream.send(b'y',time.monotonic()+1)
   self.assertEqual(state()[0],before)

 def test_late_in_backlog_after_timeout_drains_without_new_out_or_duplicate_stdout(self):
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d);queue=[];lost=[False];late=[None];outs=[];reads=[]
   def cable(frame,timeout):
    outs.append(frame);reply=exchange(frame,timeout)
    if frame[6]==7 and not lost[0]:lost[0]=True;late[0]=reply;raise TimeoutError('original IN pending')
    if late[0] is not None:
     queue.append(reply);original=late[0];late[0]=None;return original
    if queue:queue.append(reply);return queue.pop(0)
    return reply
   def read_pending(timeout):reads.append(timeout);return queue.pop(0)
   stream=host.BulkStream(cable,512,read_reply=read_pending);stream.handshake(time.monotonic()+3)
   out=bytearray();console=host.Console(stream,timeout=3,stdout=out.extend);console.command('pwd')
   self.assertEqual(state()[2],1);self.assertEqual(bytes(out).count(b'shell-status=0'),1)
   self.assertEqual(len(reads),1);self.assertEqual(outs[2],outs[3])

 def test_stale_reply_drain_timeout_never_resends_current_out(self):
  with tempfile.TemporaryDirectory() as d:
   _,exchange,state=self.start(d);outs=[]
   stream=host.BulkStream(exchange,512);stream.handshake(time.monotonic()+3)
   stream.send(b'x',time.monotonic()+1);prior=stream.last_reply
   def stale(frame,timeout):outs.append(frame);exchange(frame,timeout);return prior
   def no_in(timeout):raise TimeoutError('current reply unavailable')
   stream.exchange=stale;stream.read_reply=no_in
   with self.assertRaises(uart.ProtocolError):stream.receive(time.monotonic()+1)
   self.assertEqual(len(outs),1);self.assertTrue(stream.poisoned)
