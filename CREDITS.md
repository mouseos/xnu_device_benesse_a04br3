# Sources and licenses

Original packaging/build/test code: copyright 2026 yuma shimizu, MIT (`LICENSE`).

The MediaTek framing is based on the A04BR3 AOSP device source:
https://github.com/mouseos/android_device_benesse_a04br3,
commit `ad5e0a237b1691f49250c92a84472f2e612e07c2`,
`tools/mtk_wrap_bootimg.py`. The gzip padding and appended-DTB layout also
follow the existing EDK2 packager referenced below. No Android kernel or
ramdisk executable is embedded in the source repository or retained in the
diagnostic output.

`dts/mt8167-benesse-a04br3.dts` is copied unchanged from
https://github.com/mouseos/edk2-mtk,
commit `1982d888c765f5b12f6e0dce71f1ed8bc92ac50b`,
`Platform/Benesse/A04BR3Pkg/Blob/mt8167-benesse-a04br3.dts`.
The related packager is `Platform/Benesse/A04BR3Pkg/package_boot_image.py`.
The DTS describes the MediaTek/Benesse board and derives from the MT8167
Linux device-tree work; that source is GPL v2, not relicensed MIT.
`dts/COPYING` preserves the Linux license text from the existing A04BR3 kernel
source. Original MediaTek and other contributors retain their rights.

Porting reference: https://github.com/dipa-studio/ios-sweet,
commit `fdad0de8dc0e75c5ea84762adc03f7232dc91f19`. Its source tree is not
imported here. Apple XNU source and its original licenses live in the separate
kernel repository. No upstream Git history was imported into these repositories.

The payload format and GPT generator are original MIT-licensed port source.
Mach-O and boot ABI reference: Apple XNU `xnu-8796.141.3`,
https://github.com/apple-oss-distributions/xnu/tree/xnu-8796.141.3 .
The Android rollback layout records the existing A04BR3 AOSP port partition
definition; the XNU layout retains its required boot-chain locations and LK
attributes. No calibration data or proprietary boot images are included.

`tools/mkinit.py` and `tools/a04br3-init.S` are adapted copies of
`tools/mkinit.py` and `tools/sweet-init.S` from dipa-studio/ios-sweet,
commit `fdad0de8dc0e75c5ea84762adc03f7232dc91f19`; original contributors
retain credit. This minimal diagnostic PID 1 is built from assembly source.
It is not Apple launchd or an iOS userland distribution.

Complete root assembly follows the owner/link/cryptex/logd layout described
by `tools/mkiosroot.sh` in the same ios-sweet reference revision. The rootless
QEMU runtime, checked resume mechanism, native-xattr translation, allocation
flash planner and FUSE range-view helper are original port source. Linux HFS+
handles filesystem construction; Ubuntu packages supply the declared guest
runtime, rsync and filesystem tools. FUSE 2.x provides the range-view API.
Donor OS files remain local inputs and are not distributed in these repos.

The Rogue firmware staging tool follows the firmware-info layout and MIPS ELF
segment placement documented by Imagination Technologies / MediaTek's
`m1.11ED5425693/services/include/rgx_fw_info.h` and
`services/server/devices/rgx/rgxfwimageutils.c`, as distributed in
https://github.com/mouseos/android_kernel_benesse_TAB-A04-BR3.git at
`eb47f42e1f84fe5716ef239620725f82718a620a`. It is a new Python implementation;
no proprietary firmware or original firmware executable code is included.

`tests/fixtures/apple_bootargs/parser.c` retains exact Apple XNU boot-argument
parser function excerpts under APSL 2.0, with Apple's original notice and
`APPLE_LICENSE`. Its provenance records the fixed kernel source revision.
The fixture verifies numeric flag compaction and is not a kernel payload.
