/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Self-contained IEEE CRC32 and FIPS180-4 SHA256, for bounded capture records.
 * Input is the owned P6 image, never a live surface. No external crypto ABI.
 */
#ifndef A04_FB_CAPTURE_DIGEST_H
#define A04_FB_CAPTURE_DIGEST_H
#include <stdint.h>
#include <stddef.h>
static inline uint32_t a04_fb_crc32(const unsigned char *p,size_t n)
{
    uint32_t crc=0xffffffffU;
    for(size_t i=0;i<n;i++){crc^=p[i];for(unsigned b=0;b<8;b++)crc=(crc>>1)^(0xedb88320U& (0U-(crc&1)));}
    return ~crc;
}
static inline uint32_t a04_fb_ror(uint32_t x,unsigned n){return (x>>n)|(x<<(32-n));}
static inline void a04_fb_sha256(const unsigned char *p,size_t bytes,unsigned char out[32])
{
    static const uint32_t k[64]={
      0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t h[8]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    size_t blocks=bytes/64+1+((bytes%64)>=56);uint64_t bits=(uint64_t)bytes*8;
    for(size_t block=0;block<blocks;block++){
        uint32_t w[64]={0};
        for(unsigned j=0;j<64;j++){
            size_t at=block*64+j;unsigned char v=0;
            if(at<bytes)v=p[at];else if(at==bytes)v=0x80;
            else if(block==blocks-1 && j>=56)v=(unsigned char)(bits>>((63-j)*8));
            w[j/4]|=(uint32_t)v<<((3-j%4)*8);
        }
        for(unsigned j=16;j<64;j++){
            uint32_t x=w[j-15],y=w[j-2];
            w[j]=w[j-16]+(a04_fb_ror(x,7)^a04_fb_ror(x,18)^(x>>3))+w[j-7]+(a04_fb_ror(y,17)^a04_fb_ror(y,19)^(y>>10));
        }
        uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
        for(unsigned j=0;j<64;j++){
            uint32_t t1=hh+(a04_fb_ror(e,6)^a04_fb_ror(e,11)^a04_fb_ror(e,25))+((e&f)^(~e&g))+k[j]+w[j];
            uint32_t t2=(a04_fb_ror(a,2)^a04_fb_ror(a,13)^a04_fb_ror(a,22))+((a&b)^(a&c)^(b&c));
            hh=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=hh;
    }
    for(unsigned j=0;j<32;j++)out[j]=(unsigned char)(h[j/4]>>((3-j%4)*8));
}
#endif
