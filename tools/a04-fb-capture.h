/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Actual A04BR3 panel layout from A04BR3Framebuffer.cpp/Surface.cpp.
 * Pure bounded validation/conversion; no physical address or device access.
 */
#ifndef A04_FB_CAPTURE_H
#define A04_FB_CAPTURE_H
#include <stddef.h>
#include <stdint.h>
enum { A04_FB_WIDTH=800,A04_FB_HEIGHT=1280,A04_FB_STRIDE=3200,
       A04_FB_BYTES=4096000,A04_FB_BGRA=0x42475241 };
static inline int a04_fb_layout(size_t width,size_t height,size_t stride,
    uint32_t format,size_t element,size_t allocation,size_t *bytes)
{
    if(!bytes || width!=A04_FB_WIDTH || height!=A04_FB_HEIGHT ||
       stride!=A04_FB_STRIDE || format!=A04_FB_BGRA || element!=4 ||
       allocation<A04_FB_BYTES)return -1;
    *bytes=A04_FB_BYTES;return 0;
}
static inline int a04_fb_rgb_row(unsigned char *dst,size_t capacity,
    const unsigned char *src,size_t source_bytes)
{
    const size_t output=A04_FB_WIDTH*3;
    if(!dst || !src || capacity<output || source_bytes<A04_FB_STRIDE ||
       (uintptr_t)dst>UINTPTR_MAX-output || (uintptr_t)src>UINTPTR_MAX-A04_FB_STRIDE)return -1;
    uintptr_t a=(uintptr_t)dst,b=(uintptr_t)src;
    if(a<b+A04_FB_STRIDE && b<a+output)return -1;
    for(size_t x=0;x<A04_FB_WIDTH;x++){
        dst[3*x]=src[4*x+2];dst[3*x+1]=src[4*x+1];dst[3*x+2]=src[4*x];
    }
    return 0;
}
#endif
