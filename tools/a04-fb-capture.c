/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Read actual board scanout via the EXISTING framebuffer selector3 and
 * public IOSurface mapping API. No kernel/physical-memory ioctl or GPU task.
 * Run: a04-fb-capture /private/var/tmp/screen.ppm (new file only).
 * Or: a04-fb-capture --uart-preview LOWERCASE32HEXID
 * Preview: bounded200x320 P6,384byte chunks/base64/CRC32/finalSHA256.
 * P6 RGB output; panel source BGRA/800x1280/3200 validated before reading.
 * IOSurfaceLock(ReadOnly) is advisory in current board implementation: this
 * is a best-effort scanout copy, NOT an atomic hardware-latched screenshot.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <mach/mach.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#define A04_FB_DECLARE_IOSURFACE 1
#include "a04-fb-capture.h"
#include "a04-fb-capture-digest.h"
/* Public IOKit C API; iPhone SDK exports the framework but may omit IOKitLib.h.
 * mach_port_t is the underlying io_object_t/io_connect_t ABI on this target. */
extern CFMutableDictionaryRef IOServiceMatching(const char *);
extern mach_port_t IOServiceGetMatchingService(mach_port_t,CFDictionaryRef);
extern kern_return_t IOServiceOpen(mach_port_t,mach_port_t,uint32_t,mach_port_t *);
extern kern_return_t IOServiceClose(mach_port_t);
extern kern_return_t IOObjectRelease(mach_port_t);
extern kern_return_t IOConnectCallScalarMethod(mach_port_t,uint32_t,const uint64_t *,uint32_t,uint64_t *,uint32_t *);
static int write_all(int fd,const void *data,size_t bytes)
{
    const unsigned char *p=data;
    while(bytes){ssize_t n=write(fd,p,bytes);if(n<0 && errno==EINTR)continue;
        if(n<=0)return -1;p+=(size_t)n;bytes-=(size_t)n;}
    return 0;
}
static int frame_id_valid(const char *id)
{
    if(!id || strlen(id)!=32)return 0;
    for(unsigned i=0;i<32;i++)if(!((id[i]>='0'&&id[i]<='9')||(id[i]>='a'&&id[i]<='f')))return 0;
    return 1;
}
static void b64(const unsigned char *p,size_t n,char *out)
{
    static const char table[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t at=0;
    for(size_t i=0;i<n;i+=3){unsigned a=p[i],b=i+1<n?p[i+1]:0,c=i+2<n?p[i+2]:0;
        out[at++]=table[a>>2];out[at++]=table[((a&3)<<4)|(b>>4)];
        out[at++]=i+1<n?table[((b&15)<<2)|(c>>6)]:'=';out[at++]=i+2<n?table[c&63]:'=';}
    out[at]=0;
}
static int preview_emit(const char *id,const unsigned char *ppm,size_t bytes)
{
    char line[640],encoded[513],hex[65];unsigned char digest[32];
    a04_fb_sha256(ppm,bytes,digest);
    static const char digits[]="0123456789abcdef";
    for(unsigned i=0;i<32;i++){hex[2*i]=digits[digest[i]>>4];hex[2*i+1]=digits[digest[i]&15];}hex[64]=0;
    int n=snprintf(line,sizeof(line),"\n!A04FB1 %s BEGIN 200 320 P6 %zu\n",id,bytes);
    if(n<0 || (size_t)n>=sizeof(line) || write_all(1,line,(size_t)n))return -1;
    for(size_t offset=0;offset<bytes;offset+=384){
        size_t count=bytes-offset;if(count>384)count=384;b64(ppm+offset,count,encoded);
        n=snprintf(line,sizeof(line),"\n!A04FB1 %s DATA %zu %zu %08x %s\n",id,offset,count,a04_fb_crc32(ppm+offset,count),encoded);
        if(n<0 || (size_t)n>=sizeof(line) || write_all(1,line,(size_t)n))return -1;
    }
    n=snprintf(line,sizeof(line),"\n!A04FB1 %s END %zu %s\n",id,bytes,hex);
    return n<0 || (size_t)n>=sizeof(line) || write_all(1,line,(size_t)n)?-1:0;
}
int main(int argc,char **argv)
{
    mach_port_t service=MACH_PORT_NULL,connection=MACH_PORT_NULL,surface_port=MACH_PORT_NULL;
    IOSurfaceRef surface=NULL;unsigned char *snapshot=NULL,*preview=NULL;int locked=0,fd=-1,created=0,result=1;
    size_t bytes=0;uint32_t seed=0;const char *failure="capture failed";
    int uart_preview=argc==3 && !strcmp(argv[1],"--uart-preview") && frame_id_valid(argv[2]);
    if(!uart_preview && (argc!=2 || !argv[1] || argv[1][0]!='/')){
        fprintf(stderr,"usage: a04-fb-capture /absolute/new-file.ppm | --uart-preview LOWERCASE32HEXID\n");return 2;
    }
    CFMutableDictionaryRef matching=IOServiceMatching("AppleCLCD");
    if(!matching){failure="AppleCLCD matching unavailable";goto done;}
    /* GetMatchingService consumes matching. Do not capture a swap input or
     * an arbitrary IOSurface id; selector3 returns THIS panel's surface. */
    service=IOServiceGetMatchingService(MACH_PORT_NULL,matching);
    if(!service){failure="AppleCLCD not found";goto done;}
    if(IOServiceOpen(service,mach_task_self(),0,&connection)!=KERN_SUCCESS || !connection){
        failure="framebuffer open failed";goto done;
    }
    uint64_t size[2]={0,0};uint32_t count=2;
    if(IOConnectCallScalarMethod(connection,8,NULL,0,size,&count)!=KERN_SUCCESS ||
       count!=2 || size[0]!=A04_FB_WIDTH || size[1]!=A04_FB_HEIGHT){failure="panel dimensions mismatch";goto done;}
    uint64_t port=0;count=1;
    if(IOConnectCallScalarMethod(connection,3,NULL,0,&port,&count)!=KERN_SUCCESS ||
       count!=1 || !port || port>UINT32_MAX){failure="panel surface port unavailable";goto done;}
    surface_port=(mach_port_t)port;
    surface=IOSurfaceLookupFromMachPort(surface_port);
    if(!surface){failure="panel IOSurface lookup failed";goto done;}
    if(a04_fb_layout(IOSurfaceGetWidth(surface),IOSurfaceGetHeight(surface),IOSurfaceGetBytesPerRow(surface),
       IOSurfaceGetPixelFormat(surface),IOSurfaceGetBytesPerElement(surface),IOSurfaceGetAllocSize(surface),&bytes)){
        failure="panel IOSurface layout rejected";goto done;
    }
    snapshot=malloc(uart_preview?200*320*4:bytes);if(!snapshot){failure="snapshot allocation failed";goto done;}
    if(IOSurfaceLock(surface,kIOSurfaceLockReadOnly,&seed)!=KERN_SUCCESS){failure="panel readonly lock failed";goto done;}
    locked=1;
    const void *base=IOSurfaceGetBaseAddress(surface);
    if(!base || ((uintptr_t)base&3) || (uintptr_t)base>UINTPTR_MAX-bytes){failure="panel mapped range invalid";goto done;}
    /* Aligned volatile pixel loads also suit the existing inhibit-cache
     * mapping; avoid libc memcpy choosing wider/unaligned memory accesses.
     * Preview samples only64000 panel pixels into its own256KiB snapshot. */
    const volatile uint32_t *pixels=(const volatile uint32_t *)base;
    uint32_t *owned=(uint32_t *)snapshot;
    if(uart_preview){
        for(size_t y=0;y<320;y++)for(size_t x=0;x<200;x++)owned[y*200+x]=pixels[y*4*800+x*4];
    }else for(size_t i=0;i<bytes/4;i++)owned[i]=pixels[i];
    if(IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,&seed)!=KERN_SUCCESS){
        locked=0;failure="panel readonly unlock failed";goto done;
    }
    locked=0;
    /* Release all surface/display resources before slow filesystem/UART work. */
    CFRelease(surface);surface=NULL;
    mach_port_deallocate(mach_task_self(),surface_port);surface_port=MACH_PORT_NULL;
    IOServiceClose(connection);connection=MACH_PORT_NULL;IOObjectRelease(service);service=MACH_PORT_NULL;
    if(uart_preview){
        static const char preview_header[]="P6\n200 320\n255\n";
        size_t total=sizeof(preview_header)-1+200*320*3;
        preview=malloc(total);if(!preview){failure="preview allocation failed";goto done;}
        memcpy(preview,preview_header,sizeof(preview_header)-1);
        for(size_t y=0;y<320;y++)for(size_t x=0;x<200;x++){
            const unsigned char *pixel=snapshot+(y*200+x)*4;
            unsigned char *rgb=preview+sizeof(preview_header)-1+(y*200+x)*3;
            rgb[0]=pixel[2];rgb[1]=pixel[1];rgb[2]=pixel[0];
        }
        if(preview_emit(argv[2],preview,total)){failure="preview stream write failed";goto done;}
        fprintf(stderr,"A04FB preview OK frame=%s width=200 height=320 consistency=best-effort\n",argv[2]);
        result=0;goto done;
    }
    fd=open(argv[1],O_WRONLY|O_CREAT|O_EXCL,0600);
    if(fd<0){failure="output create failed (will not overwrite)";goto done;}created=1;
    static const char header[]="P6\n800 1280\n255\n";
    if(write_all(fd,header,sizeof(header)-1)){failure="PPM header write failed";goto done;}
    unsigned char row[A04_FB_WIDTH*3];
    for(size_t y=0;y<A04_FB_HEIGHT;y++){
        if(a04_fb_rgb_row(row,sizeof(row),snapshot+y*A04_FB_STRIDE,bytes-y*A04_FB_STRIDE) ||
           write_all(fd,row,sizeof(row))){failure="PPM row write failed";goto done;}
    }
    if(close(fd)<0){fd=-1;failure="PPM close failed";goto done;}fd=-1;
    fprintf(stderr,"A04FB capture OK width=800 height=1280 stride=3200 format=BGRA bytes=%zu seed=%u consistency=best-effort output=%s\n",bytes,seed,argv[1]);
    result=0;
done:
    if(locked)IOSurfaceUnlock(surface,kIOSurfaceLockReadOnly,&seed);
    if(surface)CFRelease(surface);
    if(surface_port)mach_port_deallocate(mach_task_self(),surface_port);
    if(connection)IOServiceClose(connection);
    if(service)IOObjectRelease(service);
    free(preview);free(snapshot);
    if(fd>=0)close(fd);
    if(result && created)unlink(argv[1]); /* Remove only our O_EXCL partial file. */
    if(result)fprintf(stderr,"A04FB capture ERR %s\n",failure);
    return result;
}
