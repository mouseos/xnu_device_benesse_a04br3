/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Explicit root-only finite UI action. Requires kernel a04br3.console=1.
 * Success means paired HID dispatch, NOT UI delivery, keyboard or GPU success.
 * After each action, use a04-fb-capture --uart-preview <unique frame id>.
 * No implicit action, persistent down, duration, power or reset command.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
/* Public IOKit C ABI (iPhone SDK can omit IOKitLib.h). Matches
 * kernel iokit/Kernel/arm/hid/A04BR3UITest.h; checked by host tests. */
extern CFMutableDictionaryRef IOServiceMatching(const char *);
extern mach_port_t IOServiceGetMatchingService(mach_port_t, CFDictionaryRef);
extern kern_return_t IOServiceOpen(mach_port_t, mach_port_t, uint32_t, mach_port_t *);
extern kern_return_t IOServiceClose(mach_port_t);
extern kern_return_t IOObjectRelease(mach_port_t);
extern kern_return_t IOConnectCallScalarMethod(mach_port_t, uint32_t, const uint64_t *, uint32_t, uint64_t *, uint32_t *);
#define A04_UI_CLIENT_TYPE 0xa04bu
#define A04_UI_SELECTOR 0u
#define A04_UI_HOME 1u
#define A04_UI_TAP 2u
static int coordinate(const char *s, unsigned limit, uint64_t *out)
{
    unsigned value = 0;
    if (!s || !*s) return 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9') return 0;
        unsigned digit = (unsigned)(*s - '0');
        if (value > (limit - 1 - digit) / 10) return 0;
        value = value * 10 + digit;
        if (value >= limit) return 0;
    }
    *out = value; return 1;
}
int main(int argc, char **argv)
{
    uint64_t request[3] = {0, 0, 0};
    if (argc == 2 && !strcmp(argv[1], "home")) request[0] = A04_UI_HOME;
    else if (argc == 4 && !strcmp(argv[1], "tap") &&
        coordinate(argv[2], 800, &request[1]) && coordinate(argv[3], 1280, &request[2])) request[0] = A04_UI_TAP;
    else { fprintf(stderr, "usage: a04-ui-test home | tap X(0..799) Y(0..1279)\n"); return 2; }
    if (geteuid() != 0) { fprintf(stderr, "a04-ui-test: root required\n"); return 1; }
    CFMutableDictionaryRef match = IOServiceMatching("A04BR3HIDButtons");
    if (!match) { fprintf(stderr, "a04-ui-test: matching failed\n"); return 1; }
    /* Matching service consumes the dictionary. Always release Mach objects. */
    mach_port_t service = IOServiceGetMatchingService(MACH_PORT_NULL, match);
    if (!service) { fprintf(stderr, "a04-ui-test: board buttons absent\n"); return 1; }
    mach_port_t connection = MACH_PORT_NULL;
    kern_return_t kr = IOServiceOpen(service, mach_task_self(), A04_UI_CLIENT_TYPE, &connection);
    IOObjectRelease(service);
    if (kr != KERN_SUCCESS || !connection) {
        if (connection) IOServiceClose(connection);
        fprintf(stderr, "a04-ui-test: private open failed 0x%x (console=1 and root required)\n", (unsigned)kr); return 1;
    }
    uint32_t count = 0;
    kr = IOConnectCallScalarMethod(connection, A04_UI_SELECTOR, request, 3, NULL, &count);
    kern_return_t close_kr = IOServiceClose(connection);
    if (kr != KERN_SUCCESS || count != 0 || close_kr != KERN_SUCCESS) {
        fprintf(stderr, "a04-ui-test: action failed 0x%x close=0x%x; do not blindly replay\n", (unsigned)kr, (unsigned)close_kr); return 1;
    }
    printf("a04-ui-test: %s paired HID dispatch complete; framebuffer confirmation required\n", argv[1]);
    return 0;
}
