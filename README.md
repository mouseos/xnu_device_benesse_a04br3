# TAB-A04-BR3 XNU device sources

This repository contains board source, boot packaging and a complete build entry
point for the first UART diagnostic. The output is **not a bootable XNU kernel**.
The current shim reports the LK handoff and Cortex-A35 registers, enters EL1h,
and inspects the runtime LK FDT for DRAM/reserved/initrd ranges, then stops.

## Repository roles

* [Kernel](https://github.com/mouseos/xnu_kernel_benesse_a04br3): copied Apple
  XNU source plus initial A04BR3 CPU target; the full kernel build is still pending.
* [Boot shim](https://github.com/mouseos/xnu_bootshim_benesse_a04br3): LK entry,
  exception vectors and UART diagnostics, built for Cortex-A35.
* This repository: DTS, board configuration, MTK/Android image construction,
  build scripts and packaging tests.

All three repositories are private. A person with access can reproduce this
stage using their contents. The diagnostic does not use kernel-repository code.
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
gh api repos/mouseos/xnu_device_benesse_a04br3/tarball/v0.2-el1-diagnostic > device.tar.gz
tar -xzf device.tar.gz --strip-components=1 -C device
shim_revision=$(python3 -c 'import json; print(json.load(open("device/sources.lock.json"))["bootshim"]["commit"])')
gh api "repos/mouseos/xnu_bootshim_benesse_a04br3/tarball/$shim_revision" > bootshim.tar.gz
tar -xzf bootshim.tar.gz --strip-components=1 -C bootshim
```

Kernel source is optional for this diagnostic and is not used by its build.
The eventual kernel build will be a separate stage. No upstream Git history is
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
512-byte kernel gzip alignment and 16 MiB partition limit. It never flashes.

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
* Ramdisk is a deterministic empty newc archive, compressed with `gzip -n -9`.
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
The prior EL2 diagnostic was hardware verified; this new stage needs its own
hardware acceptance.
Host tests do not establish device execution. The inherited watchdog can reset
the device afterwards. No GPT or LK changes are needed for this first stage.

After that, implement the observed entry state, Apple boot arguments/device-tree
conversion and XNU early UART; then timer, GICv2 and memory management; then
storage and userland. Every implemented change belongs in the owning source
repository. Temporary binary edits and uncommitted local patches are not a
supported build dependency. Research, raw UART logs and Apple userland images
belong outside these source repositories.
