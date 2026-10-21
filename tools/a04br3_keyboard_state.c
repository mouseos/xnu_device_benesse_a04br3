/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Passive iOS 16.7.16 UIKit keyboard-state reader; optional source-built diagnostic payload.
 * Build/sign as a standalone Darwin command and run with PID and cache slide.
 * No target calls, task suspension, memory writes, preferences or HID changes.
 * Layout: original UIKitCore UUID1242978a-2c2c-3781-8d6c-9777edce2804.
 * REA evidence/provenance: a04br3_keyboard_state_sources.json alongside source.
 */
#include <errno.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef A04_KBD_STATE_TEST
#include <mach/mach.h>
#include <mach/mach_vm.h>
#else
typedef unsigned mach_port_t;
extern int mach_vm_read_overwrite(mach_port_t, uint64_t, uint64_t, uint64_t, uint64_t *);
#endif

static mach_port_t keyboard_task;
static uint64_t keyboard_slide;
static unsigned keyboard_reads;
static const unsigned char keyboard_uuid[16] = {
    0x12,0x42,0x97,0x8a,0x2c,0x2c,0x37,0x81,0x8d,0x6c,0x97,0x77,0xed,0xce,0x28,0x04
};

static int
keyboard_read(uint64_t address, void *output, unsigned bytes)
{
    uint64_t got = 0;
    if (!output || !bytes || bytes > 64 || keyboard_reads >= 128 ||
        address < UINT64_C(0x100000000) || address >= UINT64_C(0x8000000000) ||
        bytes > UINT64_C(0x8000000000) - address) return -1;
    ++keyboard_reads;
    int error = mach_vm_read_overwrite(keyboard_task, address, bytes,
        (uint64_t)(uintptr_t)output, &got);
    return error ? error : got == bytes ? 0 : -1;
}

static int
keyboard_identify(void)
{
    uint32_t header[8];
    uint64_t base = UINT64_C(0x188d83000) + keyboard_slide;
    if (keyboard_read(base, header, sizeof(header)) || header[0] != 0xfeedfacf ||
        header[1] != 0x0100000c || !header[4] || header[4] > 96 ||
        header[5] < 24 || header[5] > 16384) return -1;
    unsigned offset = 32, limit = 32 + header[5], found = 0;
    for (unsigned i = 0; i < header[4]; ++i) {
        uint32_t command[2];
        if (offset > limit - 8 || keyboard_read(base + offset, command, 8) ||
            command[1] < 8 || (command[1] & 7) || command[1] > limit - offset) return -1;
        if (command[0] == 0x1b) {
            unsigned char uuid[16];
            if (found++ || command[1] != 24 || keyboard_read(base + offset + 8, uuid, 16) ||
                memcmp(uuid, keyboard_uuid, 16)) return -1;
        }
        offset += command[1];
    }
    return found == 1 && offset == limit ? 0 : -1;
}

static int
keyboard_value(const char *name, uint64_t address, unsigned bytes, uint64_t *value)
{
    uint64_t result = 0;
    int error = keyboard_read(address, &result, bytes);
    if (error) {
        printf("KBDSTATE %s unreadable=%d\n", name, error);
        return error;
    }
    printf("KBDSTATE %s=0x%llx\n", name, (unsigned long long)result);
    if (value) *value = result;
    return 0;
}

static void
keyboard_object(const char *name, uint64_t object, unsigned offset, unsigned bytes)
{
    if (object < UINT64_C(0x100000000) || object >= UINT64_C(0x8000000000) ||
        offset > UINT64_C(0x8000000000) - object) {
        printf("KBDSTATE %s unavailable-object\n", name);
        return;
    }
    (void)keyboard_value(name, object + offset, bytes, NULL);
}

static int
keyboard_snapshot(void)
{
    if (keyboard_identify()) {
        puts("KBDSTATE refusing unknown/unreadable UIKitCore UUID or load commands");
        return 2;
    }
    puts("KBDSTATE UUID matched; passive non-atomic sample; pointers/flags only");
    uint64_t app = 0, impl = 0, remote = 0;
    (void)keyboard_value("UIApplication", UINT64_C(0x1e1db31c0) + keyboard_slide, 8, &app);
    (void)keyboard_value("activeImpl", UINT64_C(0x1e1db8d90) + keyboard_slide, 8, &impl);
    (void)keyboard_value("remoteKeyboards", UINT64_C(0x1e1db3e30) + keyboard_slide, 8, &remote);
    (void)keyboard_value("remoteOnce", UINT64_C(0x1e1db3e38) + keyboard_slide, 8, NULL);
    uint64_t input_once = 0;
    if (!keyboard_value("inputUIOOPOnce", UINT64_C(0x1e1db8d00) + keyboard_slide, 8, &input_once) &&
        input_once == UINT64_MAX)
        (void)keyboard_value("inputUIOOPCached", UINT64_C(0x1e1db8cf8) + keyboard_slide, 1, NULL);
    else
        puts("KBDSTATE inputUIOOPCached unavailable-uninitialized-or-unreadable-once");
    keyboard_object("appArbiterFlagByte", app, 0xbc, 1);
    keyboard_object("implHardwareAttachedByte", impl, 0x2db, 1);
    keyboard_object("implAttachedObject", impl, 0x5c8, 8);
    keyboard_object("remoteConnection", remote, 0xd0, 8);
    keyboard_object("remoteExpectingInitialState", remote, 0x38, 1);
    keyboard_object("remoteCurrentKeyboard", remote, 0xbb, 1);
    keyboard_object("remoteCurrentState", remote, 0xd8, 8);
    keyboard_object("remoteExpectedSuppression", remote, 0x58, 1);
    keyboard_object("remoteExternalSuppression", remote, 0x5c, 4);
    keyboard_object("remoteRequiredScene", remote, 0x18, 8);
    keyboard_object("remoteSuppressedScene", remote, 0x50, 8);
    keyboard_object("remoteWindowEnabled", remote, 0x71, 1);
    printf("KBDSTATE reads=%u; no absent value interpreted as false\n", keyboard_reads);
    return 0;
}

#ifndef A04_KBD_STATE_TEST
int
main(int argc, char **argv)
{
    char *end;
    if (geteuid() != 0) {
        fputs("KBDSTATE root required; no target state read\n", stderr);
        return 77;
    }
    if (argc != 3) {
        fprintf(stderr, "usage: %s PID CACHE_SLIDE_HEX\n", argv[0]);
        return 64;
    }
    errno = 0;
    unsigned long pid = strtoul(argv[1], &end, 10);
    if (errno || argv[1][0] < '0' || argv[1][0] > '9' || *end || pid < 2 || pid > 0x7fffffff) return 64;
    errno = 0;
    keyboard_slide = strtoull(argv[2], &end, 16);
    if (errno || !((argv[2][0] >= '0' && argv[2][0] <= '9') ||
        (argv[2][0] >= 'a' && argv[2][0] <= 'f') || (argv[2][0] >= 'A' && argv[2][0] <= 'F')) || *end || (keyboard_slide & 4095) || keyboard_slide > UINT64_C(0x100000000))
        return 64;
    int error = task_for_pid(mach_task_self(), (int)pid, &keyboard_task);
    if (error) {
        printf("KBDSTATE task_for_pid error=%d; no target state read\n", error);
        return 77;
    }
    int result = keyboard_snapshot();
    (void)mach_port_deallocate(mach_task_self(), keyboard_task);
    return result;
}
#endif
