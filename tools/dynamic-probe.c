/* SPDX-License-Identifier: MIT
 * Runtime compatibility probe using the donor iOS dyld/shared cache.
 */
#include <stdlib.h>
#include <unistd.h>
#include <time.h>
#include <pthread.h>
#include <dispatch/dispatch.h>
#include <CommonCrypto/CommonDigest.h>
#include <string.h>
#define SAY(s) ((void)write(1,s,sizeof(s)-1))
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static int ready,once_count;
static dispatch_once_t once;
static int marker;
static void one_time(void *context) { (*(int *)context)++; }
static void *worker(void *unused) {
    (void)unused;
    pthread_mutex_lock(&lock);ready=1;
    pthread_cond_signal(&condition);pthread_mutex_unlock(&lock);
    dispatch_once_f(&once,&once_count,one_time);
    return &marker;
}
int main(void) {
    SAY("A04BR3 iOS pthread/dispatch/SHA512 suite: start\n");
    unsigned char *memory=malloc(4096);
    struct timespec before={0},after={0};
    int success=memory && getpid()>1 && clock_gettime(CLOCK_MONOTONIC,&before)==0;
    if (memory) {
        for (unsigned i=0;i<4096;i++) memory[i]=(unsigned char)i;
        for (unsigned i=0;i<4096;i++) if (memory[i]!=(unsigned char)i) success=0;
        free(memory);
    }
    if (clock_gettime(CLOCK_MONOTONIC,&after) || after.tv_sec<before.tv_sec ||
        (after.tv_sec==before.tv_sec && after.tv_nsec<before.tv_nsec)) success=0;
    SAY("A04BR3 iOS pthread/cond/once: start\n");
    pthread_t thread;void *result=NULL;
    pthread_mutex_lock(&lock);
    int error=pthread_create(&thread,NULL,worker,NULL);
    if (!error) {
        while (!ready) if (pthread_cond_wait(&condition,&lock)) { success=0;break; }
    } else success=0;
    pthread_mutex_unlock(&lock);
    dispatch_once_f(&once,&once_count,one_time);
    if (!error && (pthread_join(thread,&result) || result!=&marker)) success=0;
    if (once_count!=1 || !ready) success=0;
    SAY("A04BR3 iOS SHA512: start\n");
    static const unsigned char expected[64]={
        0xdd,0xaf,0x35,0xa1,0x93,0x61,0x7a,0xba,0xcc,0x41,0x73,0x49,0xae,0x20,0x41,0x31,
        0x12,0xe6,0xfa,0x4e,0x89,0xa9,0x7e,0xa2,0x0a,0x9e,0xee,0xe6,0x4b,0x55,0xd3,0x9a,
        0x21,0x92,0x99,0x2a,0x27,0x4f,0xc1,0xa8,0x36,0xba,0x3c,0x23,0xa3,0xfe,0xeb,0xbd,
        0x45,0x4d,0x44,0x23,0x64,0x3c,0xe8,0x0e,0x2a,0x9a,0xc9,0x4f,0xa5,0x4c,0xa4,0x9f};
    unsigned char digest[64];
    if (!CC_SHA512("abc",3,digest) || memcmp(digest,expected,64)) success=0;
    if (success) SAY("A04BR3 iOS pthread/cond/dispatch_once/SHA512: PASS\n");
    else SAY("A04BR3 iOS pthread/cond/dispatch_once/SHA512: FAIL\n");
    return success?0:1;
}
