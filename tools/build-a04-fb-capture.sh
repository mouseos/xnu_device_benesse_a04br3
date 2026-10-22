#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Independent capture build; does not edit UART/link stubs, root or boot image.
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)
out=${1:?output executable required (normally port/xnu/out/...)}
mkdir -p "$(dirname "$out")"
ios_sdk=$(xcrun --sdk iphoneos --show-sdk-path)
xcrun --sdk iphoneos clang -isysroot "$ios_sdk" -target arm64-apple-ios16.0 -mcpu=cortex-a35 \
    -std=c11 -O2 -Wall -Wextra -Werror \
    "$src/a04-fb-capture.c" -framework CoreFoundation -framework IOKit -framework IOSurface -lz -o "$out"
codesign --force --sign - --timestamp=none "$out"
shasum -a 256 "$src/a04-fb-capture.c" "$src/a04-fb-capture.h" "$src/a04-fb-capture-digest.h" "$src/build-a04-fb-capture.sh" "$out" > "$out.sha256"
