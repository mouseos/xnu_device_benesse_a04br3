/* SPDX-License-Identifier: MIT
 * Ordinary native read-only identity queries; no cache maintenance API.
 * Build using build-mg-formfactor-probe.sh, then include as a diagnostic job.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <sys/sysctl.h>
#include <unistd.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef CFStringRef (*mg_string_fn)(CFStringRef);
typedef int32_t (*mg_int_fn)(CFStringRef, int32_t);

static int read_sysctl(const char *key, const char *expected)
{
    char value[256] = {0};
    size_t size = sizeof(value);
    if (sysctlbyname(key, value, &size, NULL, 0) != 0 ||
        size == 0 || size > sizeof(value) || value[size - 1] != '\0') {
        printf("sysctl.%s=<unavailable>\n", key);
        return 0;
    }
    printf("sysctl.%s=%s\n", key, value);
    return strcmp(value, expected) == 0;
}

static int read_mg_string(mg_string_fn get, const char *label,
                          CFStringRef key, const char *expected)
{
    CFStringRef answer = get(key);
    char value[256] = {0};
    if (!answer) {
        printf("MGGetStringAnswer.%s=<null>\n", label);
        return 0;
    }
    /* MGGetStringAnswer's observed implementation type-checks the copied
     * result; it returns a retained CFString, released here after conversion. */
    int converted = CFStringGetCString(answer, value, sizeof(value), kCFStringEncodingUTF8);
    CFRelease(answer);
    if (!converted) {
        printf("MGGetStringAnswer.%s=<conversion-failed>\n", label);
        return 0;
    }
    printf("MGGetStringAnswer.%s=%s\n", label, value);
    return strcmp(value, expected) == 0;
}

int main(void)
{
    printf("A04BR3 MG formfactor probe v1 pid=%ld uid=%ld\n", (long)getpid(), (long)getuid());
    int product = read_sysctl("hw.product", "iPad-A04BR3");
    int machine = read_sysctl("hw.machine", "Benesse TAB-A04-BR3");
    int target = read_sysctl("hw.target", "a04br3");
    /* Use the donor shared cache through the ordinary dyld namespace.
     * Do not load the offline analysis ELF or extracted/repacked MG dylib. */
    void *library = dlopen("/usr/lib/libMobileGestalt.dylib", RTLD_LAZY | RTLD_LOCAL);
    if (!library) {
        printf("dlopen.MobileGestalt=<failed>\nverdict=UNAVAILABLE\n");
        return 2;
    }
    mg_string_fn get_string = (mg_string_fn)dlsym(library, "MGGetStringAnswer");
    mg_int_fn get_int = (mg_int_fn)dlsym(library, "MGGetSInt32Answer");
    if (!get_string || !get_int) {
        printf("dlsym.MobileGestalt=<missing-query-symbol>\nverdict=UNAVAILABLE\n");
        return 2;
    }
    /* Query APIs only. No _MGCacheValid, _MGRebuildCache,
     * _MGClearInProcessCache, sentinel, notification, or helper request. */
    int device_class = read_mg_string(get_string, "DeviceClass", CFSTR("DeviceClass"), "iPad");
    int32_t number = get_int(CFSTR("DeviceClassNumber"), INT32_MIN);
    printf("MGGetSInt32Answer.DeviceClassNumber=%ld\n", (long)number);
    /* ProductType's callback also calls the hw.product-backed getter.
     * Print it independently; old cache can affect this key too. */
    int mg_product = read_mg_string(get_string, "ProductType", CFSTR("ProductType"), "iPad-A04BR3");
    int tablet = product && machine && target && device_class && number == 3;
    printf("ProductType.matches_hw_product=%s\n", mg_product ? "yes" : "no");
    printf("verdict=%s\n", tablet ? "OBSERVED_TABLET" : "MISMATCH_OR_QUERY_FAILURE");
    /* Keep the query library loaded until process exit; it may own dispatch
     * callbacks. stdout goes to the caller's configured descriptors only. */
    return tablet ? 0 : 1;
}
