/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Small source-built Darwin UART command console and optional PID1 launcher.
 * No donor binary patches. PID1 always execs native /sbin/launchd, even if
 * console fork fails. Only the explicit boot-commit command changes boot.
 */
#include <sys/wait.h>
#include <stddef.h>
#include <dlfcn.h>
#include <zlib.h>
#include <dirent.h>
typedef unsigned long size_t;
typedef long ssize_t;
typedef unsigned int u32;
typedef unsigned long long u64;
extern int open(const char *,int,...),close(int),dup2(int,int),getpid(void),setsid(void),fork(void),execv(const char *,char *const *),waitpid(int,int *,int),ioctl(int,unsigned long,...),reboot(int);
extern ssize_t read(int,void *,size_t),write(int,const void *,size_t);
extern void _exit(int),sync(void);
extern unsigned int sleep(unsigned int);
extern off_t lseek(int,off_t,int);
extern int usleep(unsigned int);
extern size_t strlen(const char *);
extern int strcmp(const char *,const char *),memcmp(const void *,const void *,size_t),snprintf(char *,size_t,const char *,...);
extern void *memcpy(void *,const void *,size_t),*memset(void *,int,size_t);
extern int *__error(void);
#define ERRNO (*__error())
#define INFO 0x40204260UL
#define BEGIN 0x80304261UL
#define COMMIT 0x20004262UL
#define ABORT 0x20004263UL
#define COPY 0x80104264UL
struct boot_copy {u64 source,bytes;};
_Static_assert(sizeof(struct boot_copy)==16,"boot copy ABI");
struct boot_info {u32 version,flags;u64 partition_bytes,staged_bytes,image_bytes;};
struct boot_begin {u64 image_bytes;unsigned char sha256[32];u64 reserved;};
_Static_assert(sizeof(struct boot_info)==32 && sizeof(struct boot_begin)==48,"boot update ABI");
static int bootfd=-1;
static u64 staged,expected_image_bytes;
static int upload_broken,upload_verified;
/* Public SDK zlib types only: no load-time libz symbols or -lz dependency.
 * inflateInit2_(31) accepts gzip ONLY (RFC1952), not zlib or raw deflate.
 * ABI layout assertions are checked again against the real iPhoneOS SDK. */
_Static_assert(sizeof(uInt)==4 && sizeof(uLong)==8 && sizeof(z_stream)==112,
    "LP64 public zlib ABI");
_Static_assert(offsetof(z_stream,total_in)==16 && offsetof(z_stream,total_out)==40 &&
    offsetof(z_stream,zalloc)==64 && offsetof(z_stream,adler)==96,"zlib field ABI");
struct gzip_codec {
 void *handle;z_stream stream;int initialized;
 const char *(*version)(void);
 int (*init)(z_streamp,int,const char *,int);
 int (*step)(z_streamp,int);
 int (*end)(z_streamp);
 uLong (*crc)(uLong,const Bytef *,uInt);
};
static struct gzip_codec gzip_codec;
static int gzip_mode; /*0 raw/none,1 input,2 gzip END,3 finished,-1 FAILED */
static u64 compressed_expected,compressed_done;
#define GZIP_IMAGE_MAX (128ULL*1024*1024)
#define GZIP_INPUT_MAX (GZIP_IMAGE_MAX+1024*1024)
/* SDK declarations establish the dlsym callback types without referencing any
 * libz symbol in the Mach-O imports. zlib's own runtime version is supplied. */
_Static_assert(__builtin_types_compatible_p(__typeof__(&inflateInit2_),__typeof__(gzip_codec.init)),"inflateInit2 ABI");
_Static_assert(__builtin_types_compatible_p(__typeof__(&inflate),__typeof__(gzip_codec.step)),"inflate ABI");
_Static_assert(__builtin_types_compatible_p(__typeof__(&inflateEnd),__typeof__(gzip_codec.end)),"inflateEnd ABI");
_Static_assert(__builtin_types_compatible_p(__typeof__(&crc32),__typeof__(gzip_codec.crc)),"crc32 ABI");
static int gzip_release(void){
 int error=0;
 if(gzip_codec.initialized&&gzip_codec.end(&gzip_codec.stream)!=Z_OK)error=22;
 if(gzip_codec.handle&&dlclose(gzip_codec.handle))error=22;
 memset(&gzip_codec,0,sizeof(gzip_codec));return error;
}
static int gzip_prepare(void){
 /* Called only by explicit boot-zbegin, after its bounded input preflight. */
 gzip_codec.handle=dlopen("/usr/lib/libz.1.dylib",RTLD_NOW|RTLD_LOCAL);
 if(!gzip_codec.handle)return 45; /* Darwin ENOTSUP; raw remains available. */
 gzip_codec.version=(const char *(*)(void))dlsym(gzip_codec.handle,"zlibVersion");
 gzip_codec.init=(int (*)(z_streamp,int,const char *,int))dlsym(gzip_codec.handle,"inflateInit2_");
 gzip_codec.step=(int (*)(z_streamp,int))dlsym(gzip_codec.handle,"inflate");
 gzip_codec.end=(int (*)(z_streamp))dlsym(gzip_codec.handle,"inflateEnd");
 gzip_codec.crc=(uLong (*)(uLong,const Bytef *,uInt))dlsym(gzip_codec.handle,"crc32");
 if(!gzip_codec.version||!gzip_codec.init||!gzip_codec.step||!gzip_codec.end||!gzip_codec.crc){gzip_release();return 45;}
 const char *version=gzip_codec.version();
 if(!version){gzip_release();return 45;}
 int rc=gzip_codec.init(&gzip_codec.stream,16+MAX_WBITS,version,sizeof(z_stream));
 if(rc!=Z_OK){gzip_release();return rc==Z_MEM_ERROR?12:45;}
 gzip_codec.initialized=1;return 0;
}
static int gzip_failed(int error){
 gzip_release();if(bootfd>=0)close(bootfd);bootfd=-1;
 upload_broken=1;upload_verified=0;gzip_mode=-1;return error;
}
/* Kernel staging writes only. Actual boot writes are exclusively COMMIT. Any
 * partial/faulted/zero write leaves local FAILED and closes/discards staging. */
static int gzip_output(const unsigned char *data,unsigned bytes){
 if(staged>expected_image_bytes||(u64)bytes>expected_image_bytes-staged)return gzip_failed(22);
 unsigned at=0;
 while(at<bytes){ssize_t k=write(bootfd,data+at,bytes-at);
  if(k<=0 || (u64)k>bytes-at)return gzip_failed(k<0?ERRNO:5);
  at+=(unsigned)k;staged+=(u64)k;
 }
 return 0;
}
static int gzip_feed(unsigned char *data,unsigned bytes,int finishing){
 unsigned char out[4096];
 gzip_codec.stream.next_in=data;gzip_codec.stream.avail_in=bytes;
 for(;;){
  unsigned before=gzip_codec.stream.avail_in;
  gzip_codec.stream.next_out=out;gzip_codec.stream.avail_out=sizeof(out);
  int rc=gzip_codec.step(&gzip_codec.stream,finishing?Z_FINISH:Z_NO_FLUSH);
  unsigned consumed=before-gzip_codec.stream.avail_in,produced=sizeof(out)-gzip_codec.stream.avail_out;
  if(consumed>before || compressed_done>compressed_expected || consumed>compressed_expected-compressed_done)return gzip_failed(22);
  compressed_done+=consumed;
  if(rc!=Z_OK&&rc!=Z_STREAM_END&&rc!=Z_BUF_ERROR)return gzip_failed(22);
  if(rc==Z_STREAM_END && (gzip_codec.stream.avail_in || compressed_done!=compressed_expected))return gzip_failed(22);
  int error=gzip_output(out,produced);if(error)return error;
  if(rc==Z_STREAM_END){
   if(staged!=expected_image_bytes)return gzip_failed(22);
   gzip_mode=2;if(gzip_release())return gzip_failed(22);return 0;
  }
  if(!consumed&&!produced){
   if(!finishing&&!before)return 0; /* optional drain exhausted; need next input */
   return gzip_failed(22);
  }
  if(!gzip_codec.stream.avail_in && gzip_codec.stream.avail_out && !finishing)return 0;
 }
}
static int crc_hex(const char *s,u32 *out);

static char last_line[1024],last_reply[512],last_id[33];
static const char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static int say(const char *s){size_t n=strlen(s);while(n){ssize_t k=write(1,s,n);if(k<=0)return -1;s+=k;n-=(size_t)k;}return 0;}
static int number(const char *s,u64 *v){u64 n=0;if(!s || !*s)return -1;while(*s){unsigned d=(unsigned)(*s++-'0');if(d>9 || n>(~(u64)0-d)/10)return -1;n=n*10+d;}*v=n;return 0;}
static int hex(int c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1;}
static int digest(const char *s,unsigned char out[32]){if(!s || strlen(s)!=64)return -1;for(unsigned n=0;n<32;n++){int a=hex(s[n*2]),b=hex(s[n*2+1]);if(a<0||b<0)return -1;out[n]=(unsigned char)(a*16+b);}return 0;}
static int crc_hex(const char *s,u32 *out){
 if(!s||strlen(s)!=8)return -1;u32 value=0;
 for(unsigned i=0;i<8;i++){int n=hex(s[i]);if(n<0)return -1;value=(value<<4)|(unsigned)n;}*out=value;return 0;
}
static int b64value(char c){for(int n=0;n<64;n++)if(alphabet[n]==c)return n;return -1;}
static int decode(const char *s,unsigned char out[384]){
 size_t n=strlen(s);if(!n || n>512 || n%4)return -1;unsigned at=0;
 for(size_t i=0;i<n;i+=4){int a=b64value(s[i]),b=b64value(s[i+1]),c=s[i+2]=='='?0:b64value(s[i+2]),d=s[i+3]=='='?0:b64value(s[i+3]);
  if(a<0||b<0||c<0||d<0 || (s[i+2]=='='&&s[i+3]!='=') || ((s[i+2]=='='||s[i+3]=='=')&&i+4!=n))return -1;
  if((s[i+2]=='='&&(b&15)) || (s[i+3]=='='&&s[i+2]!='='&&(c&3)))return -1;
  out[at++]=(unsigned char)((a<<2)|(b>>4));if(s[i+2]!='=')out[at++]=(unsigned char)((b<<4)|(c>>2));if(s[i+3]!='=')out[at++]=(unsigned char)((c<<6)|d);
 }return (int)at;
}
static int tokens(char *s,char **v,int max){int n=0;while(*s){while(*s==' '||*s=='\t')s++;if(!*s)break;if(n==max)return -1;v[n++]=s;while(*s&&*s!=' '&&*s!='\t')s++;if(*s)*s++=0;}return n;}
static int identifier(const char *s){size_t n=strlen(s);if(!n||n>32)return 0;for(size_t i=0;i<n;i++)if(!((s[i]>='a'&&s[i]<='z')||(s[i]>='A'&&s[i]<='Z')||(s[i]>='0'&&s[i]<='9')))return 0;return 1;}
static void boot_reset(void){gzip_release();gzip_mode=0;compressed_done=compressed_expected=0;if(bootfd>=0)close(bootfd);bootfd=-1;staged=expected_image_bytes=0;upload_broken=upload_verified=0;}
static int bootopen(void){if(bootfd<0)bootfd=open("/dev/a04boot",1);return bootfd<0?ERRNO:0;}
/* Returns 1 only for an explicit reboot request, after its ACK is written. */
/* Small explicit command shell; no donor /bin/sh dependency. Bounded output.
 * Filesystem operations are requested by the physical USB/UART
 * operator. This remains a development root console, not a login service. */
extern char *getcwd(char *,size_t);
extern int chdir(const char *);
static int shell_command(int n,char **v){
 if(n==1 && !strcmp(v[0],"help"))return say("help | pwd | cd PATH | ls [PATH] | cat PATH | exec /absolute/program ARGS; status/upload/reboot are host commands\n")?5:0;
 if(n==1 && !strcmp(v[0],"pwd")){char path[1024];if(!getcwd(path,sizeof(path)))return ERRNO;return say(path)||say("\n")?5:0;}
 if(n==2 && !strcmp(v[0],"cd"))return chdir(v[1])<0?ERRNO:0;
 if((n==1||n==2) && !strcmp(v[0],"ls")){
  DIR *dir=opendir(n==2?v[1]:".");if(!dir)return ERRNO;int error=0;unsigned count=0;
  for(;;){*__error()=0;struct dirent *entry=readdir(dir);if(!entry){if(ERRNO)error=ERRNO;break;}if(++count>4096){error=27;break;}if(say(entry->d_name)||say("\n")){error=5;break;}}
  if(closedir(dir)<0&&!error)error=ERRNO;return error;
 }
 if(n==2 && !strcmp(v[0],"cat")){
  int fd=open(v[1],0);if(fd<0)return ERRNO;unsigned total=0;int error=0;char data[1025];
  for(;;){ssize_t got=read(fd,data,1024);if(!got)break;if(got<0){error=ERRNO;break;}total+=(unsigned)got;if(total>1024*1024){error=27;break;}data[got]=0;size_t at=0;while(at<(size_t)got){ssize_t sent=write(1,data+at,(size_t)got-at);if(sent<=0){error=sent<0?ERRNO:5;break;}at+=(size_t)sent;}if(error)break;}
  if(close(fd)<0&&!error)error=ERRNO;return error;
 }
 return 22;
}
static int command(const char *line,char reply[512]){
 char work[1024],*v[32];size_t len=strlen(line);if(len>=sizeof(work))return 0;memcpy(work,line,len+1);int n=tokens(work,v,31);
 if(n<3||strcmp(v[0],"@A04")||!identifier(v[1]))return 0;
 if(!strcmp(line,last_line)){memcpy(reply,last_reply,strlen(last_reply)+1);return 0;}
 if(!strcmp(v[1],last_id)){snprintf(reply,512,"\n!A04 %s ERR 22\n",v[1]);return 0;}
 int err=0,restart=0;char detail[320]="";
 if(!strcmp(v[2],"help")&&n==3)snprintf(detail,sizeof(detail),"status sample boot-begin boot-data boot-commit boot-abort reboot exec boot-zbegin boot-zdata boot-zfinish boot-dcopy shell");
 else if(!strcmp(v[2],"shell")&&n>=4){err=shell_command(n-3,&v[3]);if(!err)snprintf(detail,sizeof(detail),"shell-status=0");}
 else if(!strcmp(v[2],"sample")&&n==3){if(ioctl(0,0x20007465UL,(void *)0)<0)err=ERRNO;else snprintf(detail,sizeof(detail),"tty-status-requested");}
 else if(!strcmp(v[2],"status")&&n==3){struct boot_info info={0};err=bootopen();if(!err&&ioctl(bootfd,INFO,&info)<0)err=ERRNO;if(!err)snprintf(detail,sizeof(detail),"version=%u flags=%u partition=%llu staged=%llu image=%llu",info.version,info.flags,info.partition_bytes,info.staged_bytes,info.image_bytes);}
 else if(!strcmp(v[2],"boot-zbegin")&&n==6){
  struct boot_begin b={0};u64 compressed=0;
  if(number(v[3],&compressed)||number(v[4],&b.image_bytes)||digest(v[5],b.sha256)||
     compressed<18||compressed>GZIP_INPUT_MAX||b.image_bytes<6144||b.image_bytes>GZIP_IMAGE_MAX||(b.image_bytes&2047))err=22;
  else if(expected_image_bytes||upload_broken||gzip_mode)err=16; /* never replace staging */
  else if(!(err=gzip_prepare())){
   int opened_here=bootfd<0;struct boot_info info={0};
   err=bootopen();
   if(!err&&ioctl(bootfd,INFO,&info)<0)err=ERRNO;
   if(!err&&(info.version!=1||info.flags||b.image_bytes>info.partition_bytes))err=16;
   if(!err&&ioctl(bootfd,BEGIN,&b)<0)err=ERRNO;
   if(err){gzip_release();if(opened_here&&bootfd>=0){close(bootfd);bootfd=-1;}}
   else {gzip_mode=1;compressed_expected=compressed;compressed_done=0;expected_image_bytes=b.image_bytes;staged=0;upload_verified=0;snprintf(detail,sizeof(detail),"offset=0 raw=0");}
  }
 }
 else if(!strcmp(v[2],"boot-zdata")&&n==6){
  u64 off=0;u32 crc=0;unsigned char data[384];int count=decode(v[5],data);
  if(number(v[3],&off)||crc_hex(v[4],&crc)||count<1)err=22;
  else if(gzip_mode!=1||upload_broken||bootfd<0)err=22;
  else if(off!=compressed_done||compressed_done>compressed_expected||(u64)count>compressed_expected-compressed_done)err=22;
  else if((u32)gzip_codec.crc(0,data,(uInt)count)!=crc)err=22; /* before any inflate */
  else {err=gzip_feed(data,(unsigned)count,0);if(!err)snprintf(detail,sizeof(detail),"offset=%llu raw=%llu",compressed_done,staged);}
 }
 else if(!strcmp(v[2],"boot-zfinish")&&n==3){
  if(upload_broken||bootfd<0||(gzip_mode!=1&&gzip_mode!=2&&gzip_mode!=3))err=22;
  else if(compressed_done!=compressed_expected)err=gzip_failed(22);
  else {if(gzip_mode==1)err=gzip_feed(0,0,1);
   if(!err&&(gzip_mode!=2&&gzip_mode!=3))err=gzip_failed(22);
   if(!err){gzip_mode=3;snprintf(detail,sizeof(detail),"offset=%llu raw=%llu",compressed_done,staged);}
  }
 }
 else if(!strcmp(v[2],"boot-begin")&&n==5){struct boot_begin b={0};if(number(v[3],&b.image_bytes)||digest(v[4],b.sha256))err=22;else{boot_reset();err=bootopen();if(!err&&ioctl(bootfd,BEGIN,&b)<0)err=ERRNO;if(!err){expected_image_bytes=b.image_bytes;snprintf(detail,sizeof(detail),"offset=0");}else boot_reset();}}
 else if(!strcmp(v[2],"boot-dcopy")&&n==5){u64 src=0,len=0;
  if(number(v[3],&src)||number(v[4],&len)||!len||(src&511)||(len&511))err=22;
  else if(bootfd<0)err=9;
  else if(gzip_mode||upload_broken||upload_verified||!expected_image_bytes||staged>expected_image_bytes||len>expected_image_bytes-staged)err=22;
  else {u64 done=0;while(done<len){struct boot_copy c;c.source=src+done;c.bytes=len-done>65536?65536:len-done;
    if(ioctl(bootfd,COPY,&c)<0){err=ERRNO;upload_broken=1;break;}done+=c.bytes;staged+=c.bytes;}
   /* Raw writes are checked at file offset == staged; COPY does not move it. */
   if(!err&&lseek(bootfd,(off_t)staged,0)<0){err=ERRNO;upload_broken=1;}
   if(!err)snprintf(detail,sizeof(detail),"offset=%llu",staged);}
 }
 else if(!strcmp(v[2],"boot-data")&&n==5){u64 off=0;unsigned char data[384];int count=decode(v[4],data);if(number(v[3],&off)||off!=staged||count<1)err=22;else if(bootfd<0)err=9;else if(gzip_mode||upload_broken||upload_verified||!expected_image_bytes||staged>expected_image_bytes||(u64)count>expected_image_bytes-staged)err=22;else{int at=0;while(at<count){ssize_t k=write(bootfd,data+at,(size_t)(count-at));if(k<=0){err=k<0?ERRNO:5;upload_broken=1;break;}at+=(int)k;staged+=(u64)k;}if(!err)snprintf(detail,sizeof(detail),"offset=%llu",staged);}}
 else if(!strcmp(v[2],"boot-commit")&&n==3){if(bootfd<0)err=9;else if(upload_broken||(gzip_mode&&gzip_mode!=3)||!expected_image_bytes||staged!=expected_image_bytes)err=22;else if(ioctl(bootfd,COMMIT,0)<0){err=ERRNO;upload_broken=1;gzip_release();}else {upload_verified=1;snprintf(detail,sizeof(detail),"boot-write-verified");}}
 else if(!strcmp(v[2],"boot-abort")&&n==3){if(bootfd>=0){if(ioctl(bootfd,ABORT,0)<0)err=ERRNO;}boot_reset();}
 else if(!strcmp(v[2],"reboot")&&n==3){if(upload_broken||(expected_image_bytes&&!upload_verified))err=22;else{sync();snprintf(detail,sizeof(detail),"restarting");restart=1;}}
 else if(!strcmp(v[2],"exec")&&n>=4&&v[3][0]=='/'){v[n]=0;int pid=fork();if(pid<0)err=ERRNO;else if(!pid){boot_reset();execv(v[3],&v[3]);_exit(127);}else{int status=0;if(waitpid(pid,&status,0)<0)err=ERRNO;else snprintf(detail,sizeof(detail),"wait-status=%d",status);}}
 else err=22;
 snprintf(reply,512,"\n!A04 %s %s %s",v[1],err?"ERR":"OK",err?"":detail);
 if(err){size_t at=strlen(reply);snprintf(reply+at,512-at,"%d",err);}
 size_t at=strlen(reply);if(at+1<512){reply[at]='\n';reply[at+1]=0;}
 memcpy(last_id,v[1],strlen(v[1])+1);memcpy(last_line,line,len+1);memcpy(last_reply,reply,strlen(reply)+1);return restart;
}
/* This process never opens /dev/a04boot. Only its child owns staging fds,
 * so child exit drops the last updater reference and the kernel discards the
 * upload. Every replacement is forked from this untouched protocol state. */
static int supervise_console(void (*run_console)(void)){
 (void)setsid();
 for(unsigned attempt=0;attempt<4;attempt++){
  int pid=fork();if(pid<0){say("\nA04BR3_CONSOLE supervisor fork failed\n");return 125;}
  if(!pid){run_console();_exit(125);}
  int status=0,got;
  do{got=waitpid(pid,&status,0);}while(got<0&&ERRNO==4);
  if(got!=pid){say("\nA04BR3_CONSOLE supervisor wait failed\n");return 126;}
  char message[160];snprintf(message,sizeof(message),
   "\nA04BR3_CONSOLE child exit attempt=%u wait-status=%d; upload discarded, full BEGIN required\n",attempt+1,status);say(message);
  /* Permanent setup/open/dup errors, successful exit and unknown exit codes
   * stop. Signals (including SIGINT) and exhausted input recovery may retry. */
  int retry=WIFSIGNALED(status)||(WIFEXITED(status)&&WEXITSTATUS(status)==122);
  if(!retry)return WIFEXITED(status)?WEXITSTATUS(status):126;
  if(attempt==3){say("\nA04BR3_CONSOLE supervisor restart limit reached\n");return 127;}
  sleep(2u<<attempt);
 }
 return 127;
}
static void launch_native_with_console(void (*run_console)(void)){
 int pid=fork();if(!pid)_exit(supervise_console(run_console));
 /* Failure to create the supervisor must never prevent native PID1. */
 char *args[]={"/sbin/launchd",0};execv(args[0],args);
 say("A04BR3_CONSOLE native launchd exec failed\n");_exit(123);
}
#ifndef A04_CONSOLE_TEST
#include <termios.h>
static int console_usb;
static int open_console(void){
 if(!console_usb)return open("/dev/console",2);
 for(unsigned tries=0;tries<120;tries++){int fd=open("/dev/a04usb",2);if(fd>=0)return fd;sleep(1);}
 return -1;
}
static int configure_uart(int fd){
 struct termios t;if(tcgetattr(fd,&t)<0)return ERRNO;
 t.c_iflag&=~(IGNCR|INLCR|ISTRIP|IXON|IXOFF|BRKINT);t.c_iflag|=ICRNL|IGNBRK;
 t.c_oflag&=~ONLCR; /* The underlying console already emits CR before LF. */
 t.c_lflag|=ICANON;t.c_lflag&=~(ECHO|ECHONL|ISIG|IEXTEN);
 t.c_cflag|=CLOCAL|CREAD;
 return tcsetattr(fd,TCSANOW,&t)<0?ERRNO:0;
}
static void console(void){
 (void)setsid();int fd=open_console();if(fd<0)_exit(120);
 if(!console_usb)(void)ioctl(fd,0x20007461UL,0); /* TIOCSCTTY UART only */
 int group=getpid();if(!console_usb)(void)ioctl(fd,0x80047476UL,&group); /* TIOCSPGRP */
 for(int n=0;n<3;n++)if(dup2(fd,n)<0)_exit(121);if(fd>2)close(fd);
 int terminal_error=console_usb?0:configure_uart(0);char terminal_message[128];
 snprintf(terminal_message,sizeof(terminal_message),"\nA04BR3_CONSOLE terminal configuration errno=%d\n",terminal_error);say(terminal_message);if(terminal_error)_exit(124);
 say(console_usb?"\nA04BR3_USBCONSOLE READY version=1 canonical-ascii boot-update-explicit shell\n":"\nA04BR3_CONSOLE READY version=1 canonical-ascii boot-update-explicit\n");
 char line[1024],reply[512];unsigned at=0;int overflow=0;
 for(;;){char c;ssize_t n=read(0,&c,1);if(n<=0){int error=n<0?ERRNO:0;if(error==4)continue;if(console_usb&&(error==35||error==11)){usleep(10000);continue;}
   if(console_usb){boot_reset();at=0;overflow=0;last_line[0]=last_id[0]=last_reply[0]=0;for(int j=0;j<3;j++)close(j);int next=open_console();if(next<0)_exit(122);for(int j=0;j<3;j++)if(dup2(next,j)<0)_exit(121);if(next>2)close(next);say("\nA04BR3_USBCONSOLE RECONNECTED; full BEGIN required\n");continue;}
   static unsigned recoveries=0;char message[128];
   snprintf(message,sizeof(message),"\nA04BR3_CONSOLE input recovery=%u read=%ld errno=%d\n",++recoveries,(long)n,error);say(message);
   if(recoveries>60)_exit(122);sleep(1);
   int replacement=open("/dev/console",2);if(replacement<0)continue;
   if(configure_uart(replacement)){close(replacement);_exit(124);}(void)ioctl(replacement,0x20007461UL,0);int group=getpid();(void)ioctl(replacement,0x80047476UL,&group);
   for(int j=0;j<3;j++)if(dup2(replacement,j)<0)_exit(121);if(replacement>2)close(replacement);continue;}if(c=='\r'||c=='\n'){if(!overflow&&at){line[at]=0;reply[0]=0;int restart=command(line,reply);int acknowledged=reply[0] && say(reply)==0;if(restart && acknowledged){if(console_usb && ioctl(0,0x20005570UL,0)<0)say("\nA04BR3_USBCONSOLE reboot blocked: USB ACK not drained\n");else {sync();if(reboot(0)<0)say("\nA04BR3_CONSOLE reboot failed\n");}}}at=0;overflow=0;}else if(!overflow){if(at+1<sizeof(line))line[at++]=c;else overflow=1;}}
}
static void usb_console(void){console_usb=1;console();}
int main(int argc,char **argv){
 (void)argc;(void)argv;
 if(getpid()==1){int pid=fork();if(!pid)_exit(supervise_console(usb_console));launch_native_with_console(console);}
 console();return 0;
}
#endif
