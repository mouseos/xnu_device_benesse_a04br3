/* SPDX-License-Identifier: MIT
 * Exercise the donor iOS libSystem through its original dyld/shared cache.
 * Declarations below are stable Darwin arm64 C ABI, no SDK headers needed.
 */
extern long write(int, const void *, unsigned long);
extern void *malloc(unsigned long);
extern void free(void *);
extern int getpid(void);
struct timespec { long seconds, nanoseconds; };
extern int clock_gettime(int, struct timespec *);
int main(void) {
    static const char start[]="A04BR3 iOS dyld/libSystem child: entered main\n";
    static const char pass[]="A04BR3 iOS malloc/time/getpid: PASS\n";
    static const char fail[]="A04BR3 iOS malloc/time/getpid: FAIL\n";
    write(1,start,sizeof(start)-1);
    unsigned char *memory=malloc(4096);
    struct timespec before={0},after={0};
    int success=memory && getpid()>1 && clock_gettime(6,&before)==0;
    if (memory) {
        for (unsigned i=0;i<4096;i++) memory[i]=(unsigned char)i;
        for (unsigned i=0;i<4096;i++) if (memory[i]!=(unsigned char)i) success=0;
        free(memory);
    }
    if (clock_gettime(6,&after) || after.seconds<before.seconds ||
        (after.seconds==before.seconds && after.nanoseconds<before.nanoseconds)) success=0;
    write(1,success?pass:fail,success?sizeof(pass)-1:sizeof(fail)-1);
    return success?0:1;
}
