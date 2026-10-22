/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * Root-only development Setup skip for the A04BR3 port, run once through the
 * root console: exec /usr/appleinternal/sbin/a04-root-tweaks setup-skip
 * Mirrors dipa-studio/ios-sweet tools/sweet-root-tweaks.py (067c9b3) for the
 * Setup and activation parts: purplebuddy SetupDone/SetupFinishedAllSteps/
 * SetupVersion 11, mini-buddy flags off, no cloud configuration UI, and the
 * mobileactivationd data ark. The Activated state itself comes from the
 * bootshim ADT /chosen security-status properties (ios-sweet tools/mkadt.py).
 * Preference plists are rewritten directly; reboot right after running it.
 */
#include <CoreFoundation/CoreFoundation.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define MOBILE 501
static int failures;

static CFDictionaryRef read_plist(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;
    static unsigned char buffer[65536];
    ssize_t n = read(fd, buffer, sizeof(buffer)); close(fd);
    if (n <= 0) return NULL;
    CFDataRef data = CFDataCreate(NULL, buffer, n);
    CFPropertyListRef plist = CFPropertyListCreateWithData(NULL, data, kCFPropertyListImmutable, NULL, NULL);
    CFRelease(data);
    if (plist && CFGetTypeID(plist) != CFDictionaryGetTypeID()) { CFRelease(plist); plist = NULL; }
    return (CFDictionaryRef)plist;
}

static int write_plist(const char *path, CFDictionaryRef dict)
{
    CFDataRef data = CFPropertyListCreateData(NULL, dict, kCFPropertyListBinaryFormat_v1_0, 0, NULL);
    if (!data) return -1;
    char temp[1024]; snprintf(temp, sizeof(temp), "%s.a04new", path);
    int fd = open(temp, O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600), ok = fd >= 0;
    if (ok) ok = write(fd, CFDataGetBytePtr(data), (size_t)CFDataGetLength(data)) == CFDataGetLength(data);
    if (fd >= 0) { ok = ok && fsync(fd) == 0; close(fd); }
    CFRelease(data);
    ok = ok && chown(temp, MOBILE, MOBILE) == 0 && chmod(temp, 0600) == 0 && rename(temp, path) == 0;
    if (!ok) unlink(temp);
    return ok ? 0 : -1;
}

static void set_pref(const char *domain, const char *key, CFPropertyListRef value)
{
    /* Direct read-modify-write of /var/mobile/Library/Preferences/DOMAIN.plist
     * (mobile, 0600), as ios-sweet does offline. cfprefsd cannot be asked to
     * write another user's domain from this root console; reboot afterwards. */
    char path[512];
    snprintf(path, sizeof(path), "/private/var/mobile/Library/Preferences/%s.plist", domain);
    CFDictionaryRef old = read_plist(path);
    CFMutableDictionaryRef d = old ? CFDictionaryCreateMutableCopy(NULL, 0, old) :
        CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (old) CFRelease(old);
    CFStringRef k = CFStringCreateWithCString(NULL, key, kCFStringEncodingUTF8);
    CFDictionarySetValue(d, k, value);
    if (write_plist(path, d)) { fprintf(stderr, "a04-root-tweaks: %s write failed errno=%d\n", path, errno); failures++; }
    else printf("pref %s %s\n", domain, key);
    CFRelease(k); CFRelease(d);
}

static void data_ark(void)
{
    const char *base = "/private/var/containers/Data/System";
    DIR *dir = opendir(base);
    if (!dir) { fprintf(stderr, "a04-root-tweaks: %s errno=%d\n", base, errno); failures++; return; }
    char found[512] = "";
    for (struct dirent *e; (e = readdir(dir));) {
        if (e->d_name[0] == '.') continue;
        char meta[512]; snprintf(meta, sizeof(meta), "%s/%s/.com.apple.mobile_container_manager.metadata.plist", base, e->d_name);
        CFDictionaryRef m = read_plist(meta);
        if (!m) continue;
        CFTypeRef id = CFDictionaryGetValue(m, CFSTR("MCMMetadataIdentifier"));
        if (id && CFGetTypeID(id) == CFStringGetTypeID() && CFEqual(id, CFSTR("com.apple.mobileactivationd")))
            snprintf(found, sizeof(found), "%s/%s", base, e->d_name);
        CFRelease(m);
    }
    closedir(dir);
    if (!found[0]) { fputs("a04-root-tweaks: no mobileactivationd system container yet\n", stderr); failures++; return; }
    char lib[600], internal[640], ark[700];
    snprintf(lib, sizeof(lib), "%s/Library", found); snprintf(internal, sizeof(internal), "%s/internal", lib);
    snprintf(ark, sizeof(ark), "%s/data_ark.plist", internal);
    mkdir(lib, 0755); mkdir(internal, 0755);
    if (chown(internal, MOBILE, MOBILE) || chmod(internal, 0755)) { failures++; return; }
    CFMutableDictionaryRef d;
    CFDictionaryRef old = read_plist(ark);
    if (old) { d = CFDictionaryCreateMutableCopy(NULL, 0, old); CFRelease(old); }
    else d = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(d, CFSTR("-ActivationState"), CFSTR("Activated"));
    CFDictionarySetValue(d, CFSTR("-BrickState"), kCFBooleanFalse);
    CFDictionarySetValue(d, CFSTR("-BuildVersion"), CFSTR("20H392"));
    CFDictionarySetValue(d, CFSTR("-ProductVersion"), CFSTR("16.7.16"));
    CFDictionarySetValue(d, CFSTR("-DataMigrationCompleted"), kCFBooleanTrue);
    CFDictionarySetValue(d, CFSTR("-PreSydroDataMigrationCompleted"), kCFBooleanTrue);
    if (write_plist(ark, d)) { fprintf(stderr, "a04-root-tweaks: %s write failed errno=%d\n", ark, errno); failures++; }
    else printf("data ark %s\n", ark);
    CFRelease(d);
}

int main(int argc, char **argv)
{
    if (argc != 2 || strcmp(argv[1], "setup-skip")) { fputs("usage: a04-root-tweaks setup-skip\n", stderr); return 2; }
    if (geteuid() != 0) { fputs("a04-root-tweaks: root required\n", stderr); return 1; }
    int eleven = 11; CFNumberRef version = CFNumberCreate(NULL, kCFNumberIntType, &eleven);
    set_pref("com.apple.purplebuddy", "SetupDone", kCFBooleanTrue);
    set_pref("com.apple.purplebuddy", "SetupFinishedAllSteps", kCFBooleanTrue);
    set_pref("com.apple.purplebuddy", "SetupVersion", version);
    set_pref("com.apple.purplebuddy", "daemonizeNeedsToRun", kCFBooleanFalse);
    CFRelease(version);
    static const char *mini[] = {"ShouldRunDiagnosticsMiniBuddy", "ShouldRunHSA2MiniBuddy",
        "ShouldRunKeychainSyncMiniBuddy", "ShouldRunMesaMiniBuddy", "ShouldRunPaymentMiniBuddy",
        "ShouldRunStandardMiniBuddy", "SilentICDPUpgrade"};
    for (unsigned i = 0; i < sizeof(mini) / sizeof(mini[0]); i++)
        set_pref("com.apple.purplebuddy.notbackedup", mini[i], kCFBooleanFalse);
    set_pref("com.apple.managedconfiguration.notbackedup", "LockdownActivationIndicatesCloudConfigurationAvailable", kCFBooleanFalse);
    set_pref("com.apple.springboard", "MCForceShowCloudConfigurationUI", kCFBooleanFalse);
    data_ark();
    printf("a04-root-tweaks: setup-skip %s (%d failures); reboot to apply\n", failures ? "incomplete" : "applied", failures);
    return failures ? 1 : 0;
}
