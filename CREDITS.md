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
