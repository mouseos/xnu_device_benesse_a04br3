#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output directory required}
mkdir -p "$out/stubs"
cp "$src/ios-libSystem.tbd" "$out/stubs/libSystem.tbd"
xcrun clang -target arm64-apple-ios16.0 -mcpu=cortex-a35 -O2 \
    -ffreestanding -fno-stack-protector -nostdlib \
    -Wl,-e,_main -Wl,-platform_version,ios,16.0,16.0 \
    -L"$out/stubs" -lSystem "$src/dynamic-probe.c" -o "$out/dynamic-probe"
codesign --force --sign - --timestamp=none "$out/dynamic-probe"
xcrun clang -target arm64-apple-ios16.0 -mcpu=cortex-a35 -c \
    "$src/a04br3-init.S" -o "$out/a04br3-init.o"
python3 "$src/mkinit.py" "$out/a04br3-init.o" "$out/a04br3-init"
