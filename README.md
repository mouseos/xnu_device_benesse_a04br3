> **Work in progress / 作業中:** Experimental XNU port for Benesse TAB-A04-BR3 (MT8167). XNU entry has been observed, but the device currently watchdog-reboots; a working OS is not available.

# TAB-A04-BR3 XNU device sources

This repository contains board source, MTK boot packaging, raw source-kernel
payload generation and GPT layout generation. The current stage targets entry
into a source-built XNU kernel. Initial kernel entry has been observed on
hardware; the previous image then faulted after MMU enable. This revision
corrects the 39-bit virtual layout and still needs hardware verification.

## Repository roles

* [Kernel](https://github.com/mouseos/xnu_kernel_benesse_a04br3): copied Apple
  XNU source plus A04BR3 CPU target and a macOS/Xcode build entry point.
* [Boot shim](https://github.com/mouseos/xnu_bootshim_benesse_a04br3): LK entry,
  exception vectors and UART diagnostics, built for Cortex-A35.
* This repository: DTS, board configuration, MTK/Android image construction,
  build scripts and packaging tests.

All three repositories are public and under active development. This stage
can be reproduced using their contents. The diagnostic does not use kernel-repository code.
`sources.lock.json` records the exact sibling revisions for this stage.

## Build from repository contents

On Ubuntu 24.04, install the ordinary build dependencies:

```sh
sudo apt-get install gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu python3 device-tree-compiler gzip git gh
```

Authenticate `gh` with an account that has repository access. In a new empty
directory, obtain the device source and its locked shim source without cloning:

```sh
mkdir device bootshim
gh api repos/mouseos/xnu_device_benesse_a04br3/tarball/main > device.tar.gz
tar -xzf device.tar.gz --strip-components=1 -C device
shim_revision=$(python3 -c 'import json; print(json.load(open("device/sources.lock.json"))["bootshim"]["commit"])')
gh api "repos/mouseos/xnu_bootshim_benesse_a04br3/tarball/$shim_revision" > bootshim.tar.gz
tar -xzf bootshim.tar.gz --strip-components=1 -C bootshim
```

Kernel source is optional for this diagnostic and is not used by its build.
The kernel build is a separate macOS stage described below. No upstream Git history is
needed. New repositories can be initialized around these copies when developing.

The build accepts both clean Git checkouts at the locked revision and source
archives: the tracked source-file manifest verifies archives without history.
No boot image, SDK, prebuilt DTB or file from an earlier local experiment is
required for this diagnostic.

```sh
python3 device/tools/build.py --bootshim bootshim --out out/diagnostic
python3 -m unittest discover -s device/tests -v
```

The script compiles the checked-in DTS with `dtc`, compiles the shim, constructs
the Android v0 and MediaTek section headers from `board.json`, and emits
`boot.img`, `build.json`, and `SHA256SUMS` under the output directory. It verifies
the generated section lengths, SHA-1 boot ID, decompression, appended DTB,
512-byte kernel gzip alignment and the new 128 MiB partition limit. It never flashes.

`build.json` records source revisions and tool versions. A second complete build
in another directory can be compared with `cmp out/diagnostic/boot.img
out/second/boot.img`. Cross-version compiler byte identity is not assumed.

## MediaTek format

The framing follows the working AOSP device source's
`tools/mtk_wrap_bootimg.py` and the EDK2 A04BR3 packager:

* Android boot header v0, 2048-byte page size, configured load addresses.
* `bootopt=64S3,32N2,64N2` starts at Android header offset `0x40`. MT8167
  preloader parses these fixed bytes before LK loads the image. Removing or
  moving this prefix selects secure 32-bit defaults and can crash LK at its
  ATF-log SMC, before any shim code executes. The builder rejects that layout.
* Both kernel and ramdisk have a 512-byte section header with magic
  `0x58881688`, payload length and `KERNEL` / `ROOTFS` name, then `0xff` fill.
* Kernel payload is `gzip -n -9` of the source-built shim, zero padding to a
  512-byte boundary, then the source-built board DTB.
* Without a kernel, ramdisk is a deterministic empty gzip newc archive.
  With `--kernel`, ROOTFS carries the raw versioned XNU payload.
* Android SHA-1 ID is recalculated over section data and little-endian lengths.

The full board DTS is copied from the existing EDK2 port, with provenance in
`CREDITS.md`. Its inherited memory description is not a verified usable-memory
map for XNU. The diagnostic does not dereference or allocate from the DTB;
runtime LK fixups and reserved regions must be checked before kernel use.

## Hardware acceptance and next stages

Acceptance requires a fresh UART capture containing
`XNU_A04BR3_SHIM_DIAGNOSTIC_V1`, register values and `SHIM_DIAGNOSTIC_DONE`.
The EL1 stage additionally requires `XNU_A04BR3_EL1_DIAGNOSTIC_V2`,
`CurrentEL=0x0000000000000004`, parsed FDT ranges and `SHIM_EL1_DIAGNOSTIC_DONE`.
The EL2 and EL1 diagnostics were hardware verified. The new XNU handoff needs
its own hardware acceptance.
Host tests do not establish device execution. The inherited watchdog can reset
the device afterwards. No GPT or LK changes are needed for this first stage.

After that, implement the observed entry state, Apple boot arguments/device-tree
conversion and XNU early UART; then timer, GICv2 and memory management; then
storage and userland. Every implemented change belongs in the owning source
repository. Temporary binary edits and uncommitted local patches are not a
supported build dependency. Research, raw UART logs and Apple userland images
belong outside these source repositories.

## Build and package the source kernel

Obtain the kernel revision from `sources.lock.json` using the same GitHub
archive method. On macOS with Xcode 15.4 selected:

```sh
kernel_revision=$(python3 -c 'import json; print(json.load(open("device/sources.lock.json"))["kernel"]["commit"])')
DEVELOPER_DIR=/Applications/Xcode_15.4.app/Contents/Developer GITHUB_SHA="$kernel_revision" python3 kernel/build-support/build.py --out out/kernel --install-kdk
```

The KDK location, dependency sources and versions are declared in the kernel
repository. Its Actions workflow can retain a runner for SSH repairs; see
`build-support/SSH.md` there. Successful source builds produce
`kernel.development.a04br3` and `build.json`. Transfer those two outputs to the
packaging machine, then run:

```sh
python3 device/tools/build.py --bootshim bootshim --kernel out/kernel/kernel.development.a04br3 --kernel-build-json out/kernel/build.json --out out/xnu-entry
```

The packager checks the locked source revision and successful build record,
extracts loadable Mach-O segments, and creates the loader's raw payload. This
is loading a newly built kernel, not modifying a proprietary kernel image.
File-only CTF debugging information is not loaded into guest memory. The new
input address is 0x80000000; the kernel's selected managed physical window is
0x60000000..0x78000000, with FDT reservations checked by the shim at runtime.
Acceptance requires `XNU_A04BR3_KERNEL_ENTRY` from XNU itself after the shim's
`XNU_HANDOFF_JUMP`. Neither image construction nor that jump message proves
kernel execution.

## GPT preparation (not yet hardware verified)

`layouts/xnu.json` describes this device's 30,535,680-sector, 512-byte-sector
user area. It preserves pre-boot/calibration partitions, recovery, LK/LK2 and
TEE locations, and flashinfo. `boot` moves to LBA 294912 (144 MiB) and grows to
128 MiB. `system` occupies the remainder from LBA 557056 to 30501887; Android
metadata/vendor/cache/userdata entries are removed. The old boot location is
unassigned. eMMC hardware boot regions containing preloader are outside GPT.
`layouts/android-gsi.json` retains the earlier partition definition for rollback.

```sh
python3 device/tools/gpt.py --layout device/layouts/xnu.json --out out/gpt-xnu
python3 device/tools/gpt.py --layout device/layouts/android-gsi.json --out out/gpt-android
```

The generator creates and validates both GPT copies. The secondary copy must
be written at the byte offset in `gpt.json` (15634251264 for this layout).
mtkclient's named `w gpt` operation writes only the primary copy; it does not
install the backup copy automatically. Do not use the new larger image on the
old 16 MiB boot layout. On-device geometry, relocated-boot lookup and a complete
LK/TEE restoration procedure must be verified before applying this layout.
These source tools never invoke mtkclient or write a device.

The A04BR3 bootstrap uses T1SZ=25 (39-bit upper virtual addresses). Both the
payload builder and shim reject lower, noncanonical kernel windows. The kernel
target uses the normal ARM64 link base 0xfffffff007004000 and disables the Apple
MacOSX large-memory VA layout. The previous 0xfffffe0007004000 link base was
outside that bootstrap range; it must not be reused with this board setup.

Minimal userspace bring-up: add `--bringup-init` to the source image build
command with `--kernel` and `--kernel-build-json`. Clang assembles
`tools/a04br3-init.S` for Cortex-A35; `mkinit.py` creates a static ad-hoc signed
Mach-O and the payload includes it as a RAMDisk. `rd=md0` selects Apple's
built-in memory device and mockfs exposes it as `/sbin/launchd`. This PID 1
prints a banner and periodic heartbeat through `/dev/console`; it does not
provide an iOS or macOS environment. No prebuilt executable is required.

For a regular-file diagnostic root, run `python3 tools/mkhfsroot.py --out DIR`
on macOS with the declared Xcode toolchain, then pass its `hfs-root.img` as
`tools/build.py --rootfs IMAGE` instead of `--bringup-init`. The checked-in
script builds PID 1, creates HFS+ using hdiutil, and includes the marker
`/etc/a04br3-stage`. The diagnostic PID 1 reads and prints that marker.
Filesystem UUIDs/timestamps are assigned by hdiutil; reconstruction uses the
same sources and steps but does not promise a byte-identical HFS image.
Payload v3 encodes zero runs in the RAMDisk and validates all bounds before
loading. The physical boot image must still fit the actual 16 MiB partition.

Hardware milestone (2026-10-20): kernel `89fea0f`, bootshim `556aa15`,
device `41dc5bc` mounted the generated HFS+ RAMDisk on TAB-A04-BR3,
executed the diagnostic PID 1 and printed the regular file
`/etc/a04br3-stage`. Periodic console output continued without an observed
panic. This verifies the diagnostic root path, not Apple launchd or an iOS
or macOS environment. The next image optionally probes inherited MSDC0
using `--msdc-probe`; eMMC operation is not yet hardware verified.

Persistent diagnostic root: build the same generated HFS image, write it to
`userdata`, and package the source kernel with `--emmc-root` instead of a
RAMDisk option. This selects `rd=emmcroot0` and the inherited MSDC0 probe.
The kernel validates the GPT header and entry-table CRC, finds `userdata`,
checks its bounds, and exposes only read access through the BSD driver.
Writing the root image replaces the existing userdata filesystem. The GPT
and boot-chain partition locations remain unchanged. Root data is generated
by `tools/mkhfsroot.py`, not a downloaded or patched executable image.

The unsigned development HFS root uses the explicit
`a04br3.unsealed_root=1` argument supplied by `--emmc-root`. Only unsupported
HFS root-authentication ioctls are allowed; a reported authentication failure
remains fatal. This does not establish an Apple verified boot chain.

Dyld child probe (original iPhone X iOS 16.7.16, build 20H392):

1. Run `bash tools/build-dynamic-probe.sh OUT` on the declared macOS/Xcode
   host. It builds the static diagnostic PID 1 and an iOS arm64 executable
   importing malloc/free/write/getpid/clock_gettime from libSystem. The stub
   is source text, not a copy of Apple runtime code. The child runs through
   `/usr/lib/dyld`; PID 1 polls its status and continues its heartbeat.
2. Obtain the iPhone10,3/iPhone10,6 16.7.16 20H392 restore IPSW locally.
   Read its BuildManifest.plist, select the d22ap identity, and extract the
   `OS` and `Cryptex1,SystemOS` DMG paths from that identity's Manifest.
   Mount/read those APFS volumes read-only with apfs-fuse. Copy the original
   OS `/usr/lib/dyld` to `ARTIFACTS/macho/dyld`, and every
   `System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64*` file from
   SystemOS to `ARTIFACTS/cache/`. No userland binary patches or reconstructed
   cache-library exports are used in the boot root.
3. On Linux install `hfsprogs` (tested 540.1.linux3-5build3), then run
   `python3 tools/prepare-dyld-root.py --artifacts ARTIFACTS --probes OUT --out ROOT`.
   It creates an unmounted 4 GiB HFS+ image, writes a source-generated catalog
   and allocation bitmap with original donor file data, and runs fsck read-only.
   The generator is limited to this small ASCII-name, regular-file tree;
   it rejects metadata layouts requiring overflow extents/node maps.
4. Package the existing source kernel with `--emmc-root --dyld-probe`, and
   write the generated HFS to userdata plus the matching boot image. The
   explicit development option permits iOS binaries in this macOS-targeted
   XNU configuration and chooses ignition level 1. Donor binaries, cache
   files, and generated filesystems stay outside all source repositories.

Linux without sudo can extract hfsprogs' deb and invoke its mkfs/fsck by
absolute path (`--mkfs` and `--fsck`); no kernel filesystem mount is needed.
The static init can also be rebuilt with Clang targeting arm64-apple-ios16.0
and `tools/mkinit.py`, using the same checked-in assembly.

The expanded `dynamic-probe.c` exercises a real pthread with mutex/condition
synchronization, dispatch_once_f across parent/worker threads, and the SHA-512
known answer for "abc", using the same donor runtime. `.github/workflows/probes.yml`
builds only these programs on the declared Xcode host and retains opt-in SSH
for fixes; it does not rebuild XNU or install a KDK.

For a root already written from a known local generated image and mounted
read-only on the device, `tools/hfsdelta.py OLD NEW --out DIR
--partition-start BYTES --partition-bytes BYTES` creates a bounded mtkclient
script of changed HFS blocks. Both images must have equal aligned sizes;
all writes must stay inside the supplied partition bounds. Run
`mtk script DIR/write.mtk`. Obtain the partition start/capacity from the
actual validated GPT, not a guessed device layout. Check every command's
write result; an mtkclient process exit code alone is insufficient. This
reconstructs a new source-generated filesystem, without altering donor code.
Do not use an old image as baseline if the device filesystem has been changed.

For a bounded Apple launchd PID 1 bring-up, pass `--pid1 apple` to
`prepare-dyld-root.py`. This copies the original `ARTIFACTS/macho/launchd`
without code or signature patches. The diagnostic init is retained at
`/usr/local/libexec/a04br3-diagnostic-init`; keep the previous diagnostic
root image for rollback. This minimal image does not contain the complete
iOS boot-task executables or daemon configuration and is not a full iOS root.
Kernel SHA-1 support is required before launchd configures loopback IPv6
(ios-sweet 0032). SHA-384/512 kernel providers are separate from successful
CommonCrypto userland tests. Rebuild the kernel from committed source first.

Apple PID 1 images now require `--os-root DONOR_OS_ROOT`, pointing to the
read-only mounted OS component selected from the d22ap BuildManifest.
`boot_tasks.py` reads launchd's Mach-O `__config` plist as data and copies
the listed boot executables without modification. Missing RequireRun
executables fail generation; absent optional tasks are recorded in
root-build.json. Internal relative aliases are copied as identical regular
files; links escaping the donor root are rejected. SystemVersion.plist is
also copied when present. This is still a boot-task subset, not a full root.
Use `build.py --platform-root --emmc-root --dyld-probe` for this development
image. The explicit flag substitutes read-only root membership for the
missing Apple trust cache when assigning platform status; it is not secure
boot. XML entitlements must match the code directory, and missing/DER-only
entitlements are not granted.

HFS boot-task roots include the original donor /sbin/mount_hfs and the
identical helper at /Library/Filesystems/hfs.fs/Contents/Resources/mount_hfs,
which mount may choose as its fallback path. Bootshim emits a 48-byte
/chosen/boot-manifest-hash naming the A04BR3 development preboot namespace
(SHA-384 of the documented fixed development label), not an authenticated
Apple boot manifest. Root and block device remain read-only in this stage.

`--writable-root --emmc-root` explicitly enables a04br3.root_rw=1 and
a04br3.emmc_rw=1. The block driver accepts writes only to GPT userdata
minor 1; whole-disk and raw character writes stay denied. CMD24 single-block
PIO waits for CMD13 programming completion; synchronize ioctls flush an
enabled card cache with EXT_CSD FLUSH_CACHE. No clock/timing changes are made.
This is a development non-journaled HFS root; the explicit mode also permits
its dirty-root remount after a crash. It does not repair filesystem damage.
The development platform policy continues on this explicitly writable root.
After the first writable boot, never use a previous generated root image as
a delta baseline: restore a complete source-generated image for root updates.
Boot-only fixes can reuse the current root.

For development keybag bring-up only, pass `--dev-keystore` to both root
preparation and boot generation (the latter also requires --writable-root).
The software AppleKeyStore is disabled by default and is always unlocked
when explicitly enabled; it implements no SEP, passcode, encryption or
cryptographic wrapping. Unsupported operations fail. Bootshim describes
the board's absence of Apple effaceable storage. Root generation writes a
plain source-generated plist with an empty KeyBagKeys data value, root-owned
0600, under keybags mode 0700. This is a test fixture, not a usable encrypted
keybag. It satisfies the file-presence check on the already-loaded test bag
path; native donor keybagd remains unmodified. Credits: ios-sweet 0038.

`prepare-dyld-root.py --bootstrap-layout --os-root DONOR_OS_ROOT` imports
the donor's complete usr/share/zoneinfo.default subtree, retaining file
contents and permission bits. It preserves the donor guest targets of
/var, /etc, /tmp and /usr/share/zoneinfo as real HFS symbolic links; no host
link traversal is used to import timezone entries. Runtime directories
include /private/var/run, tmp (1777), db/timezone, the launchd log parent
and /Library/Preferences/FeatureFlags. The stage marker moves under
/private/etc, remaining accessible through the native /etc link.
HFS symlink records use S_IFLNK and the Apple HFS slnk/rhap Finder fields
from hfs_vnops.c; tests read back the catalog, data fork and fsck result.
Use a full root image write after any writable boot. This bootstrap subset
still does not contain all iOS daemons, application bundles or services.

Boot arguments include the device repository revision (or `archive` when
Git metadata is absent), so UART captures can distinguish root-layout trials
that reuse the same kernel and bootshim.

For the next service stage, pass `--service-profile config/bootstrap-services.json`
to root preparation and `--development-launchd-cache` to boot generation.
The latter emits the documented development argument `launchd_unsecure_cache=1`
(ios-sweet 0039 bring-up notes); no valid Apple signature is claimed for a
source-generated configuration. The profile explicitly selects notifyd alone,
retains its donor job fields and adds RunAtLoad for this test. The executable
is copied unmodified. The generated version-7 cache and matching job plist
contain only the selected services; app extensions and other daemon jobs are
not enabled in this stage. Selection and overrides are stored as source,
not applied as in-place binary patches. This tests basic launchd services,
not the full iOS service graph or GUI. The unsigned-cache option is off by
default and requires the explicit development platform-root option.

### Complete donor root in a rootless Linux VM

`tools/fullroot_vm.py` builds a full 14 GiB case-sensitive, journaled HFSX
image using the Linux HFS+ driver inside QEMU. The host never mounts the
image and needs no sudo. Ubuntu 24.04 amd64 tools are listed in
`config/fullroot-runtime.json`; the unsigned guest kernel and optional
matching module package have explicit upstream download locations. Guest
modules may also come from the installed matching version. The source
builds its initramfs from the declared host tools and their libraries.

Mount all three d22ap donor components read-only with apfs-fuse, as selected
by BuildManifest: OS, Cryptex1,SystemOS and Cryptex1,AppOS. Supply the actual
`root` directory of each FUSE mount. Keep donor artifacts outside Git.
First use `--probe` to verify the native filesystem path without donor data:

```
python3 tools/fullroot_vm.py --probe --out PROBE_OUT \
  --kernel-deb KERNEL_DEB --mkfs MKFS_HFSPLUS --fsck FSCK_HFSPLUS --log PROBE_LOG
python3 tools/fullroot_vm.py --os-root OS_ROOT --system-root SYSTEM_ROOT \
  --app-root APP_ROOT --probes SOURCE_BUILT_PROBES --out FULL_ROOT_OUT \
  --kernel-deb KERNEL_DEB --mkfs MKFS_HFSPLUS --fsck FSCK_HFSPLUS --log COPY_LOG
```

The guest exports are read-only and its only writable disk is the new image.
Native rsync preserves modes, numeric ownership, symlinks and hard links.
The complete native OS and both cryptex trees are copied. Missing root aliases
for cryptex files, including the shared cache, are hard links rather than
extra copies. Native xattrs are exported separately and written with Linux's
`osx.` HFS namespace mapping. APFS compression metadata and compressed
ResourceFork storage are omitted when decompressed bytes are copied; they
must not be applied to an uncompressed file. Other xattr failures stop the
build. BSD flags not exposed by Linux's FUSE stat interface are not claimed
to be preserved. Apple's fsck normalization pass and a final read-only check
must pass before the image is reported ready.

The original complete launchd cache and daemon graph are retained. No
_PanicOnCrash key or daemon program is patched. Explicit development additions
are the unlocked test systembag, logd restore directories (ios-sweet
mkiosroot.sh reference), runtime directories, and source-built diagnostics.
This root still needs the development kernel/boot arguments and compatible
hardware drivers; copying all files is not proof of an iOS GUI boot.

`layouts/xnu-full-root.json` reclaims Android system/vendor/cache/metadata:
boot becomes 128 MiB at LBA 294912; userdata starts at LBA 557056 and ends
before flashinfo. Other retained boot-chain partitions keep their addresses.
The 14 GiB root fits this layout, but does not fit the old userdata partition.
Generate and validate both GPT copies with `tools/gpt.py`. GPT, boot and root
writes must use the new layout's exact offsets; do not write this image using
the old userdata address. Source copying and VM tests do not modify a device.

Build full-donor boot images with --full-donor-root together with the
existing explicit writable/platform-root, development KeyStore and unsigned
launchd-cache options. This requests only CSR_ALLOW_UNRESTRICTED_FS plus
the unauthenticated development-root policy, so the native shared cache
can map without BSD SF_RESTRICTED flags unavailable through Linux FUSE.
Other SIP restrictions are not requested to be disabled by this option.
The VM initramfs includes nls_utf8 for the native HFS+ mount.

A failed full copy may be retried with --resume using the same output and
donor roots. The saved plan and runtime are checked, an exclusive process
lock prevents duplicate writers, and the image is checked before mounting.
Rsync resumes the complete tree without recopying unchanged file data; source
guest scripts and initramfs are regenerated without reinstalling the kernel.

Readonly 9p exports use a 1 MiB message size and loose caching. Sources must
remain immutable during the copy; this avoids repeated small host/FUSE reads
and metadata round trips across the full donor tree.

Full-donor boot uses the hardware-FDT-derived 1504 MiB managed interval
0x60000000..0xbe000000. Reported firmware reservations are excluded; the
Android RAMDisk payload moves to 0x50000000 to avoid overlap. The default
small diagnostic profile is unchanged. Shim tests cover contiguous banks,
reject a gap and reject a reservation inside the managed interval. This
expanded profile still requires hardware verification before being claimed
working; it does not imply all 2 GiB is available to XNU.

For the complete root, `tools/fullroot_flash.py --image HFS_IMG --boot BOOT_IMG
--layout layouts/xnu-full-root.json --out PLAN_DIR` emits a bounded mtkclient
script. It writes every allocated filesystem block plus reserved volume
headers, skipping only unallocated storage. This is a complete filesystem
write and needs neither an old target image nor a device readback. Small holes
may be included to reduce command count. Native copy_file_range stages source
ranges without a userspace buffer when supported. Boot and backup GPT follow
the root data, and primary GPT activates the new layout last. This saves
transferring filesystem free space while keeping all live data and metadata.
Never run the script until the final fsck-verified root-build.json exists.
