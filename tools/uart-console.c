/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Small source-built Darwin UART command console and optional PID1 launcher.
 * No donor binary patches. PID1 always execs native /sbin/launchd, even if
 * console fork fails. Only the explicit boot-commit command changes boot.
 */
typedef unsigned long size_t;
typedef long ssize_t;
typedef unsigned int u32;
typedef unsigned long long u64;
extern int open(const char *,int,...),close(int),dup2(int,int),getpid(void),setsid(void),fork(void),execv(const char *,char *const *),waitpid(int,int *,int),ioctl(int,unsigned long,...),reboot(int);
extern ssize_t read(int,void *,size_t),write(int,const void *,size_t);
extern void _exit(int),sync(void);
extern size_t strlen(const char *);
extern int strcmp(const char *,const char *),memcmp(const void *,const void *,size_t),snprintf(char *,size_t,const char *,...);
extern void *memcpy(void *,const void *,size_t),*memset(void *,int,size_t);
extern int *__error(void);
#define ERRNO (*__error())
#define INFO 0x40204260UL
#define BEGIN 0x80304261UL
#define COMMIT 0x20004262UL
#define ABORT 0x20004263UL
struct boot_info {u32 version,flags;u64 partition_bytes,staged_bytes,image_bytes;};
struct boot_begin {u64 image_bytes;unsigned char sha256[32];u64 reserved;};
_Static_assert(sizeof(struct boot_info)==32 && sizeof(struct boot_begin)==48,"boot update ABI");
static int bootfd=-1;
static u64 staged;
static char last_line[1024],last_reply[512],last_id[33];
static const char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
static int say(const char *s){size_t n=strlen(s);while(n){ssize_t k=write(1,s,n);if(k<=0)return -1;s+=k;n-=(size_t)k;}return 0;}
static int number(const char *s,u64 *v){u64 n=0;if(!s || !*s)return -1;while(*s){unsigned d=(unsigned)(*s++-'0');if(d>9 || n>(~(u64)0-d)/10)return -1;n=n*10+d;}*v=n;return 0;}
static int hex(int c){if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;return -1;}
static int digest(const char *s,unsigned char out[32]){if(!s || strlen(s)!=64)return -1;for(unsigned n=0;n<32;n++){int a=hex(s[n*2]),b=hex(s[n*2+1]);if(a<0||b<0)return -1;out[n]=(unsigned char)(a*16+b);}return 0;}
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
static int bootopen(void){if(bootfd<0)bootfd=open("/dev/a04boot",1);return bootfd<0?ERRNO:0;}
/* Returns 1 only for an explicit reboot request, after its ACK is written. */
static int command(const char *line,char reply[512]){
 char work[1024],*v[32];size_t len=strlen(line);if(len>=sizeof(work))return 0;memcpy(work,line,len+1);int n=tokens(work,v,31);
 if(n<3||strcmp(v[0],"@A04")||!identifier(v[1]))return 0;
 if(!strcmp(line,last_line)){memcpy(reply,last_reply,strlen(last_reply)+1);return 0;}
 if(!strcmp(v[1],last_id)){snprintf(reply,512,"\n!A04 %s ERR 22\n",v[1]);return 0;}
 int err=0,restart=0;char detail[320]="";
 if(!strcmp(v[2],"help")&&n==3)snprintf(detail,sizeof(detail),"status boot-begin boot-data boot-commit boot-abort reboot exec");
 else if(!strcmp(v[2],"status")&&n==3){struct boot_info info={0};err=bootopen();if(!err&&ioctl(bootfd,INFO,&info)<0)err=ERRNO;if(!err)snprintf(detail,sizeof(detail),"version=%u flags=%u partition=%llu staged=%llu image=%llu",info.version,info.flags,info.partition_bytes,info.staged_bytes,info.image_bytes);}
 else if(!strcmp(v[2],"boot-begin")&&n==5){struct boot_begin b={0};if(number(v[3],&b.image_bytes)||digest(v[4],b.sha256))err=22;else{err=bootopen();if(!err&&ioctl(bootfd,BEGIN,&b)<0)err=ERRNO;if(!err){staged=0;snprintf(detail,sizeof(detail),"offset=0");}}}
 else if(!strcmp(v[2],"boot-data")&&n==5){u64 off=0;unsigned char data[384];int count=decode(v[4],data);if(number(v[3],&off)||off!=staged||count<1)err=22;else if(bootfd<0)err=9;else{int at=0;while(at<count){ssize_t k=write(bootfd,data+at,(size_t)(count-at));if(k<=0){err=k<0?ERRNO:5;break;}at+=(int)k;staged+=(u64)k;}if(!err)snprintf(detail,sizeof(detail),"offset=%llu",staged);}}
 else if(!strcmp(v[2],"boot-commit")&&n==3){if(bootfd<0)err=9;else if(ioctl(bootfd,COMMIT,0)<0)err=ERRNO;else snprintf(detail,sizeof(detail),"boot-write-verified");}
 else if(!strcmp(v[2],"boot-abort")&&n==3){if(bootfd>=0){if(ioctl(bootfd,ABORT,0)<0)err=ERRNO;close(bootfd);bootfd=-1;}staged=0;}
 else if(!strcmp(v[2],"reboot")&&n==3){sync();snprintf(detail,sizeof(detail),"restarting");restart=1;}
 else if(!strcmp(v[2],"exec")&&n>=4&&v[3][0]=='/'){v[n]=0;int pid=fork();if(pid<0)err=ERRNO;else if(!pid){execv(v[3],&v[3]);_exit(127);}else{int status=0;if(waitpid(pid,&status,0)<0)err=ERRNO;else snprintf(detail,sizeof(detail),"wait-status=%d",status);}}
 else err=22;
 snprintf(reply,512,"\n!A04 %s %s %s",v[1],err?"ERR":"OK",err?"":detail);
 if(err){size_t at=strlen(reply);snprintf(reply+at,512-at,"%d",err);}
 size_t at=strlen(reply);if(at+1<512){reply[at]='\n';reply[at+1]=0;}
 memcpy(last_id,v[1],strlen(v[1])+1);memcpy(last_line,line,len+1);memcpy(last_reply,reply,strlen(reply)+1);return restart;
}
#ifndef A04_CONSOLE_TEST
static void console(void){
 (void)setsid();int fd=open("/dev/console",2|0x20000);if(fd<0)_exit(120);for(int n=0;n<3;n++)if(dup2(fd,n)<0)_exit(121);if(fd>2)close(fd);
 say("\nA04BR3_CONSOLE READY version=1 canonical-ascii boot-update-explicit\n");
 char line[1024],reply[512];unsigned at=0;int overflow=0;
 for(;;){char c;ssize_t n=read(0,&c,1);if(n<=0){if(n<0&&ERRNO==4)continue;_exit(122);}if(c=='\r'||c=='\n'){if(!overflow&&at){line[at]=0;reply[0]=0;int restart=command(line,reply);if(reply[0])say(reply);if(restart){sync();if(reboot(0)<0)say("\nA04BR3_CONSOLE reboot failed\n");}}at=0;overflow=0;}else if(!overflow){if(at+1<sizeof(line))line[at++]=c;else overflow=1;}}
}
int main(int argc,char **argv){
 (void)argc;(void)argv;
 if(getpid()==1){int pid=fork();if(!pid)console();char *args[]={"/sbin/launchd",0};execv(args[0],args);say("A04BR3_CONSOLE native launchd exec failed\n");_exit(123);}
 console();return 0;
}
#endif
