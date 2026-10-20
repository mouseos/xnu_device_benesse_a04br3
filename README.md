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
