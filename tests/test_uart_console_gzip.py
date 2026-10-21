# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual production parser + host libz, mocked staging fd (never hardware).
No -lz: tests also exercise absent loader/symbol and libz cleanup contracts.
"""
import base64
import gzip
import hashlib
import pathlib
import os
import subprocess
import tempfile
import unittest
import zlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = r'''
#define A04_CONSOLE_TEST 1
#define open test_open
#define close test_close
#define write test_write
#define ioctl test_ioctl
#define dlopen test_dlopen
#define dlsym test_dlsym
#define dlclose test_dlclose
#include "SOURCE"
#undef open
#undef close
#undef write
#undef ioctl
#undef dlopen
#undef dlsym
#undef dlclose
extern void *dlopen(const char *,int),*dlsym(void *,const char *);
extern int dlclose(void *);
#include <assert.h>
#include <stdarg.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int *__error(void){return &errno;}
static unsigned begins,commits,opens,closes,loads,unloads,ends,steps,ioctls;
static unsigned char output[32*1024*1024];
static size_t output_n;static int mode,partial,begin_error,commit_error;
static int (*real_step)(z_streamp,int),(*real_end)(z_streamp);
static int step(z_streamp s,int flush){steps++;if(mode==3)return Z_OK;return real_step(s,flush);}
static int end(z_streamp s){ends++;int rc=real_end(s);return mode==6?Z_STREAM_ERROR:rc;}
void *test_dlopen(const char *path,int flags){assert(!strcmp(path,"/usr/lib/libz.1.dylib"));loads++;if(mode==1)return 0;return dlopen("libz.so.1",flags);}
void *test_dlsym(void *h,const char *name){if(mode==2&&!strcmp(name,"inflate"))return 0;
 void *p=dlsym(h,name);if(!strcmp(name,"inflate")){real_step=(int (*)(z_streamp,int))p;return (void *)step;}
 if(!strcmp(name,"inflateEnd")){real_end=(int (*)(z_streamp))p;return (void *)end;}return p;}
int test_dlclose(void *h){unloads++;return dlclose(h);}
int test_open(const char *path,int flags,...){assert(!strcmp(path,"/dev/a04boot")&&flags==1);opens++;return 9;}
int test_close(int fd){assert(fd==9);closes++;return 0;}
ssize_t test_write(int fd,const void *p,size_t bytes){assert(fd==9&&output_n+bytes<=sizeof(output));
 if(mode==4){if(partial){errno=5;return -1;}partial=1;if(bytes>1)bytes=1;}
 if(mode==5)return 0;
 memcpy(output+output_n,p,bytes);output_n+=bytes;return (ssize_t)bytes;}
int test_ioctl(int fd,unsigned long cmd,...){assert(fd==9);ioctls++;va_list ap;va_start(ap,cmd);void *p=va_arg(ap,void *);va_end(ap);
 if(cmd==INFO){if(mode==9){errno=5;return -1;}struct boot_info *i=p;*i=(struct boot_info){1,mode==8?4u:0,128ULL*1024*1024,0,0};return 0;}
 if(cmd==BEGIN){if(begin_error){errno=12;return -1;}begins++;output_n=0;return 0;}
 if(cmd==COMMIT){commits++;if(commit_error){errno=5;return -1;}return 0;}return 0;}
int main(int argc,char **argv){assert(argc==2);char line[2048],r[512];
 while(fgets(line,sizeof(line),stdin)){
  size_t n=strlen(line);if(n&&line[n-1]=='\n')line[n-1]=0;
  if(!strncmp(line,"MODE ",5)){mode=atoi(line+5);continue;}
  if(!strcmp(line,"BEGINFAIL")){begin_error=1;continue;}
  if(!strcmp(line,"COMMITFAIL")){commit_error=1;continue;}
  r[0]=0;int restart=command(line,r);printf("%s",r);
  printf("STATE bytes=%zu staged=%llu comp=%llu expected=%llu mode=%d broken=%d verified=%d fd=%d begins=%u commits=%u opens=%u closes=%u loads=%u unloads=%u ends=%u steps=%u restart=%d\n",output_n,staged,compressed_done,expected_image_bytes,gzip_mode,upload_broken,upload_verified,bootfd,begins,commits,opens,closes,loads,unloads,ends,steps,restart);
 }
 FILE *file=fopen(argv[1],"wb");assert(file);assert(fwrite(output,1,output_n,file)==output_n);assert(!fclose(file));
 boot_reset();printf("CLEAN loads=%u unloads=%u ends=%u fd=%d\n",loads,unloads,ends,bootfd);return 0;
}
'''
class GzipConsole(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp = tempfile.TemporaryDirectory()
  cls.path = pathlib.Path(cls.tmp.name)
  source=HARNESS.replace('SOURCE',str(ROOT/'tools/uart-console.c'))
  (cls.path/'test.c').write_text(source)
  subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-unused-function',
                  '-fsanitize=address,undefined',str(cls.path/'test.c'),'-ldl','-o',str(cls.path/'test')],check=True)
 @classmethod
 def tearDownClass(cls): cls.tmp.cleanup()
 def run_wire(self,commands):
  out=self.path/'output'
  r=subprocess.run([str(self.path/'test'),str(out)],input='\n'.join(commands)+'\n',text=True,capture_output=True,timeout=15)
  self.assertEqual(r.returncode,0,r.stderr+r.stdout)
  return r.stdout,out.read_bytes()
 def begin(self,packed,raw,id='b',rawlen=None,complen=None):
  return f'@A04 {id} boot-zbegin {len(packed) if complen is None else complen} {len(raw) if rawlen is None else rawlen} {hashlib.sha256(raw).hexdigest()}'
 def chunk(self,data,offset,id):
  return f'@A04 {id} boot-zdata {offset} {zlib.crc32(data):08x} {base64.b64encode(data).decode()}'
 def stream(self,packed):
  return [self.chunk(packed[at:at+384],at,'d'+str(at)) for at in range(0,len(packed),384)]
 def data(self): return bytes((i*43+i//7)%256 for i in range(16384))
 def test_real_gzip_stream_and_same_id_retries(self):
  raw=self.data();packed=gzip.compress(raw,mtime=0);cmd=[self.begin(packed,raw)]
  for line in self.stream(packed): cmd += [line,line]
  cmd += ['@A04 f boot-zfinish','@A04 f boot-zfinish','@A04 c boot-commit','@A04 c boot-commit','@A04 r reboot']
  text,result=self.run_wire(cmd);self.assertEqual(result,raw);self.assertNotIn(' ERR ',text)
  self.assertIn('commits=1',text);self.assertIn('restart=1',text);self.assertIn('unloads=1 ends=1',text)
 def test_loader_is_lazy_unavailable_and_missing_symbol_preserve_raw(self):
  raw=self.data();packed=gzip.compress(raw)
  for mode in (1,2):
   text,result=self.run_wire(['@A04 s status',f'MODE {mode}',self.begin(packed,raw),'@A04 rb boot-begin 4 '+'0'*64,'@A04 rd boot-data 0 dGVzdA==','@A04 rc boot-commit'])
   self.assertIn('loads=0',text);self.assertIn('!A04 b ERR 45',text);self.assertIn('!A04 rc OK',text);self.assertEqual(result,b'test')
 def test_busy_optional_begin_never_destroys_existing_stage(self):
  raw=self.data();packed=gzip.compress(raw)
  text,result=self.run_wire(['@A04 rb boot-begin 4 '+'0'*64,'@A04 rd boot-data 0 dGVzdA==',self.begin(packed,raw),'@A04 rc boot-commit'])
  self.assertIn('!A04 b ERR 16',text);self.assertIn('loads=0',text);self.assertEqual(result,b'test');self.assertIn('!A04 rc OK',text)
 def test_crc_offset_and_base64_rejected_before_inflate(self):
  raw=self.data();packed=gzip.compress(raw);line=self.chunk(packed[:384],0,'d')
  bad=line.replace(f'{zlib.crc32(packed[:384]):08x}','00000000')
  text,result=self.run_wire([self.begin(packed,raw),bad,self.chunk(packed[:384],1,'off'),'@A04 fmt boot-zdata 0 0 !!!!',line.replace('@A04 d','@A04 good')])
  states=[x for x in text.splitlines() if x.startswith('STATE')]
  for state in states[:4]: self.assertIn('steps=0',state)
  self.assertNotIn('broken=1',text);self.assertGreater(len(result),0)
 def test_truncated_missing_and_trailing_streams_fail_closed(self):
  raw=self.data();packed=gzip.compress(raw)
  variants=[(packed[:-1],len(packed)-1),(packed[:-8],len(packed)),(packed+b'X',len(packed)+1),(packed+packed,len(packed)*2)]
  for blob,length in variants:
   text,_=self.run_wire([self.begin(blob,raw,complen=length)]+self.stream(blob)+['@A04 f boot-zfinish','@A04 c boot-commit','@A04 r reboot'])
   self.assertIn('broken=1',text);self.assertNotIn('commits=1',text);self.assertNotIn('restart=1',text);self.assertIn('ends=1',text)
 def test_crc_correct_corrupt_gzip_and_non_gzip_rejected(self):
  raw=self.data();packed=gzip.compress(raw)
  corrupt=bytearray(packed);corrupt[-8]^=1
  for blob in (bytes(corrupt),zlib.compress(raw),b'x'*40):
   text,_=self.run_wire([self.begin(blob,raw)]+self.stream(blob)+['@A04 f boot-zfinish','@A04 c boot-commit','@A04 r reboot'])
   self.assertIn('broken=1',text);self.assertNotIn('commits=1',text);self.assertNotIn('restart=1',text)
 def test_uncompressed_limit_and_mismatched_lengths(self):
  raw=self.data();packed=gzip.compress(raw)
  for length in (len(raw)-2048,len(raw)+2048):
   text,_=self.run_wire([self.begin(packed,raw,rawlen=length)]+self.stream(packed)+['@A04 f boot-zfinish','@A04 c boot-commit'])
   self.assertIn('broken=1',text);self.assertNotIn('commits=1',text)
  for length in (0,6143,6145,128*1024*1024+2048,18446744073709551616):
   text,_=self.run_wire([self.begin(packed,raw,rawlen=length)])
   self.assertIn('!A04 b ERR 22',text);self.assertIn('loads=0',text)
  for length in (0,17,129*1024*1024+1,18446744073709551616):
   text,_=self.run_wire([self.begin(packed,raw,complen=length)])
   self.assertIn('!A04 b ERR 22',text);self.assertIn('loads=0',text)
 def test_partial_zero_io_and_no_progress_failure_release(self):
  raw=self.data();packed=gzip.compress(raw)
  for mode in (3,4,5):
   text,_=self.run_wire([self.begin(packed,raw),f'MODE {mode}',self.chunk(packed[:384],0,'d'),'@A04 c boot-commit','@A04 r reboot','@A04 a boot-abort'])
   self.assertIn('broken=1',text);self.assertNotIn('restart=1',text);self.assertNotIn('commits=1',text);self.assertIn('unloads=1 ends=1',text)
 def test_abort_reset_and_kernel_commit_error(self):
  raw=self.data();packed=gzip.compress(raw)
  text,_=self.run_wire([self.begin(packed,raw),self.chunk(packed[:10],0,'d'),'@A04 a boot-abort'])
  self.assertIn('unloads=1 ends=1',text);self.assertIn('mode=0 broken=0',text)
  text,_=self.run_wire(['BEGINFAIL',self.begin(packed,raw)])
  self.assertIn('!A04 b ERR 12',text);self.assertIn('unloads=1 ends=1',text)
  text,_=self.run_wire([self.begin(packed,raw)]+self.stream(packed)+['@A04 early boot-commit','@A04 f boot-zfinish','COMMITFAIL','@A04 c boot-commit','@A04 r reboot'])
  self.assertIn('!A04 early ERR 22',text);self.assertIn('!A04 c ERR 5',text);self.assertNotIn('restart=1',text)
 def test_real_image_when_explicitly_supplied(self):
  image=os.environ.get('A04_GZIP_TEST_IMAGE')
  if not image: self.skipTest('set A04_GZIP_TEST_IMAGE for offline real boot image verification')
  raw=pathlib.Path(image).read_bytes();packed=gzip.compress(raw,compresslevel=6,mtime=0)
  text,result=self.run_wire([self.begin(packed,raw)]+self.stream(packed)+['@A04 f boot-zfinish','@A04 c boot-commit'])
  self.assertNotIn(' ERR ',text);self.assertEqual(hashlib.sha256(result).digest(),hashlib.sha256(raw).digest())
  self.assertEqual(result,raw);self.assertIn('commits=1',text)
  print(f'actual production stream raw={len(raw)} gzip={len(packed)} sha256={hashlib.sha256(result).hexdigest()}')
 def test_release_error_and_unknown_kernel_state_fail_closed(self):
  raw=self.data();packed=gzip.compress(raw)
  text,_=self.run_wire([self.begin(packed,raw),'MODE 6']+self.stream(packed)+['@A04 f boot-zfinish','@A04 c boot-commit','@A04 r reboot'])
  self.assertIn('broken=1',text);self.assertNotIn('commits=1',text);self.assertNotIn('restart=1',text)
  for mode in (8,9):
   text,_=self.run_wire([f'MODE {mode}',self.begin(packed,raw)])
   self.assertIn('begins=0',text);self.assertIn('expected=0',text);self.assertIn('unloads=1 ends=1',text)
 def test_no_loadtime_libz_import_or_extra_source_pin(self):
  text=subprocess.check_output(['nm','-u',str(self.path/'test')],text=True)
  self.assertNotRegex(text,r'\b(inflate|inflateEnd|inflateInit2_|zlibVersion|crc32)(@|\s|$)')
  deps=subprocess.check_output(['ldd',str(self.path/'test')],text=True)
  self.assertNotIn('libz.so',deps)
  build=(ROOT/'tools/build-uart-console.sh').read_text();self.assertNotIn('-lz',build)
  stub=(ROOT/'tools/uart-libSystem.tbd').read_text()
  for name in ('_dlopen','_dlsym','_dlclose'): self.assertIn(name,stub)
if __name__=='__main__': unittest.main()
